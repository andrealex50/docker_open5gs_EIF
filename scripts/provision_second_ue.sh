#!/usr/bin/env bash
set -euo pipefail

SOURCE_IMSI=${SOURCE_IMSI:-001011234567895}
UE2_IMSI=${UE2_IMSI:-001011234567896}
UE2_IMEISV=${UE2_IMEISV:-4370816125816152}
MONGO_CONTAINER=${MONGO_CONTAINER:-mongo}

if ! docker inspect "${MONGO_CONTAINER}" >/dev/null 2>&1; then
  echo "Cannot access MongoDB container '${MONGO_CONTAINER}'" >&2
  exit 1
fi

docker exec "${MONGO_CONTAINER}" mongosh open5gs --quiet --eval "
const source = db.subscribers.findOne({imsi: '${SOURCE_IMSI}'});
if (!source) throw new Error('source subscriber ${SOURCE_IMSI} not found');
delete source._id;
source.imsi = '${UE2_IMSI}';
source.imeisv = '${UE2_IMEISV}';
source.security.sqn = NumberLong('0');
source.slice.forEach(slice => {
  slice._id = ObjectId();
  slice.session.forEach(session => session._id = ObjectId());
});
db.subscribers.replaceOne({imsi: '${UE2_IMSI}'}, source, {upsert: true});
printjson(db.subscribers.findOne({imsi: '${UE2_IMSI}'}, {_id: 0}));
"
