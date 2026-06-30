import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import app


class EnergyAttributionTests(unittest.TestCase):
    def response(self):
        return {
            "supi": "imsi-test",
            "event": "UE_ENERGY",
            "durationSec": 10.0,
            "energyInfo": {"energy": 1.5},
        }

    def source_sample(self, value=100.0):
        return {
            "source": "scaphandre_prometheus",
            "metric": "host_rapl_energy",
            "unit": "joules",
            "value": value,
        }

    @patch("app.store_energy_attribution")
    def test_dynamic_mode_subtracts_baseline_before_attribution(self, store):
        with patch.dict(os.environ, {
            "ENERGY_ATTRIBUTION_MODE": "dynamic_traffic_share",
            "ENERGY_HOST_IDLE_BASELINE_W": "2.0",
        }, clear=False):
            result = app.attributed_energy_response(
                self.response(), self.source_sample(), 25, 100
            )

        self.assertEqual(result["energyInfo"]["energy"], 20.0)
        self.assertEqual(result["attribution"]["baselineEnergy"], 20.0)
        self.assertEqual(result["attribution"]["dynamicEnergy"], 80.0)
        self.assertEqual(result["attribution"]["ratio"], 0.25)
        store.assert_called_once()

    @patch("app.store_energy_attribution")
    def test_baseline_is_clamped_to_measured_energy(self, store):
        with patch.dict(os.environ, {
            "ENERGY_ATTRIBUTION_MODE": "dynamic_traffic_share",
            "ENERGY_HOST_IDLE_BASELINE_W": "2.0",
        }, clear=False):
            result = app.attributed_energy_response(
                self.response(), self.source_sample(10.0), 1, 1
            )

        self.assertEqual(result["energyInfo"]["energy"], 0.0)
        self.assertEqual(result["attribution"]["baselineEnergy"], 10.0)
        self.assertEqual(result["attribution"]["dynamicEnergy"], 0.0)
        store.assert_called_once()

    @patch("app.store_energy_attribution")
    def test_legacy_mode_keeps_full_measured_energy(self, store):
        with patch.dict(os.environ, {
            "ENERGY_ATTRIBUTION_MODE": "traffic_share",
            "ENERGY_HOST_IDLE_BASELINE_W": "2.0",
        }, clear=False):
            result = app.attributed_energy_response(
                self.response(), self.source_sample(), 25, 100
            )

        self.assertEqual(result["energyInfo"]["energy"], 25.0)
        self.assertEqual(result["attribution"]["allocatableEnergy"], 100.0)
        store.assert_called_once()


class EnergySourceTests(unittest.TestCase):
    def setUp(self):
        app.external_energy_samples.clear()

    @patch("app.query_prometheus_value", return_value=12.5)
    def test_normalized_prometheus_sample_contains_url(self, query_value):
        start = datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 6, 30, 10, 1, tzinfo=timezone.utc)

        with patch.dict(os.environ, {"PROMETHEUS_URL": "http://metrics:9090"}):
            sample = app.normalized_prometheus_sample(
                start,
                end,
                "test_source",
                "test_metric",
                "increase(test_energy[{window}])",
            )

        self.assertEqual(sample["value"], 12.5)
        self.assertEqual(sample["metadata"]["prometheus_url"], "http://metrics:9090")
        self.assertEqual(sample["metadata"]["promql"], "increase(test_energy[60s])")
        query_value.assert_called_once()

    @patch("app.query_prometheus_energy")
    def test_traffic_mode_does_not_query_prometheus(self, query_prometheus):
        start = datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 6, 30, 10, 1, tzinfo=timezone.utc)

        with patch.dict(os.environ, {
            "ENERGY_SOURCE": "traffic",
            "POWERAPI_COMPARISON_ENABLED": "false",
        }):
            sample = app.query_and_store_energy_source(start, end)

        self.assertIsNone(sample)
        query_prometheus.assert_not_called()

    @patch("app.get_mongo_db", return_value=None)
    def test_external_sample_is_selected_by_exact_window(self, mongo):
        model = app.ExternalEnergySample(
            source="external_wattmeter",
            metric="wall_energy",
            unit="J",
            window_start="2026-06-30T10:00:00Z",
            window_end="2026-06-30T10:01:00Z",
            value=42.25,
        )
        document = app.normalize_external_energy_sample(model)
        app.store_external_energy_sample(document)

        with patch.dict(os.environ, {
            "ENERGY_SOURCE": "external_wattmeter",
            "EXTERNAL_ENERGY_SOURCE": "external_wattmeter",
            "EXTERNAL_ENERGY_METRIC": "wall_energy",
            "POWERAPI_COMPARISON_ENABLED": "false",
        }):
            selected = app.query_and_store_energy_source(
                datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 6, 30, 10, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(selected["value"], 42.25)
        self.assertEqual(selected["unit"], "joules")
        self.assertEqual(mongo.call_count > 0, True)

    def test_retention_adds_bson_datetime(self):
        with patch.dict(os.environ, {"ENERGY_RETENTION_DAYS": "7"}):
            stored = app.document_with_expiry({"value": 1.0})

        self.assertEqual(stored["value"], 1.0)
        self.assertIsInstance(stored["expires_at"], datetime)
        self.assertIsNotNone(stored["expires_at"].tzinfo)

    def test_external_sample_rejects_non_joule_unit(self):
        model = app.ExternalEnergySample(
            window_start="2026-06-30T10:00:00Z",
            window_end="2026-06-30T10:01:00Z",
            value=10.0,
            unit="watts",
        )

        with self.assertRaises(app.HTTPException):
            app.normalize_external_energy_sample(model)

    @patch("app.query_external_energy")
    @patch("app.normalized_prometheus_sample", return_value=None)
    def test_powerapi_falls_back_to_pushed_normalized_sample(
        self, prometheus_sample, external_sample
    ):
        expected = {
            "source": "powerapi_smartwatts",
            "metric": "estimated_software_energy",
            "unit": "joules",
            "value": 7.5,
        }
        external_sample.return_value = expected
        start = datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 6, 30, 10, 1, tzinfo=timezone.utc)

        result = app.query_powerapi_energy(start, end)

        self.assertEqual(result, expected)
        prometheus_sample.assert_called_once()
        external_sample.assert_called_once_with(
            start,
            end,
            "powerapi_smartwatts",
            "estimated_software_energy",
        )

    @patch("app.get_mongo_db", return_value=None)
    def test_external_adjacent_windows_are_prorated_and_aggregated(self, mongo):
        for start, end, value in (
            ("10:00:00", "10:00:30", 30.0),
            ("10:00:30", "10:01:00", 60.0),
        ):
            model = app.ExternalEnergySample(
                source="external_wattmeter",
                metric="measured_energy",
                window_start=f"2026-06-30T{start}Z",
                window_end=f"2026-06-30T{end}Z",
                value=value,
            )
            app.store_external_energy_sample(
                app.normalize_external_energy_sample(model)
            )

        selected = app.query_external_energy(
            datetime(2026, 6, 30, 10, 0, 15, tzinfo=timezone.utc),
            datetime(2026, 6, 30, 10, 0, 45, tzinfo=timezone.utc),
        )

        self.assertEqual(selected["value"], 45.0)
        self.assertEqual(selected["metadata"]["inputSamples"], 2)
        self.assertEqual(mongo.call_count > 0, True)

    @patch("app.get_mongo_db", return_value=None)
    def test_external_window_with_gap_is_unavailable(self, mongo):
        model = app.ExternalEnergySample(
            window_start="2026-06-30T10:00:00Z",
            window_end="2026-06-30T10:00:20Z",
            value=20.0,
        )
        app.store_external_energy_sample(
            app.normalize_external_energy_sample(model)
        )

        selected = app.query_external_energy(
            datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 30, 10, 1, tzinfo=timezone.utc),
        )

        self.assertIsNone(selected)
        self.assertEqual(mongo.call_count > 0, True)


if __name__ == "__main__":
    unittest.main()
