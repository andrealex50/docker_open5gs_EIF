#!/usr/bin/env bash
set -e

docker rm -f notify-server 2>/dev/null || true

docker run --rm -it \
  --name notify-server \
  --network docker_open5gs_default \
  --ip 172.22.0.45 \
  node:20-slim \
  node -e '
const http2 = require("http2");

const server = http2.createServer();

server.on("stream", (stream, headers) => {
  const method = headers[":method"];
  const path = headers[":path"];
  let body = "";

  stream.on("error", err => console.error("Stream error:", err.message));
  stream.on("data", chunk => body += chunk);
  stream.on("end", () => {
    console.log("\n--- REQUEST RECEIVED ---");
    console.log("Method:", method);
    console.log("Path:", path);
    console.log("Headers:", headers);
    console.log("Body:", body);

    if (!stream.destroyed) {
      stream.respond({ ":status": 204 });
      stream.end();
    }
  });
});

server.listen(9998, "0.0.0.0", () => {
  console.log("HTTP/2 h2c notify server listening on 0.0.0.0:9998");
});
'
