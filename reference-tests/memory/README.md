# STREAM memory reference test

Requires the STREAM **5.10** source and a C compiler with OpenMP support. The upstream source is
<https://raw.githubusercontent.com/jeffhammond/STREAM/6703f7504a38a8da96b353cadafa64d3c2d7a2d3/stream.c>, pinned by SHA-256
`c388924eb140fda95f534cdb808ae7f1f8ebb18da41d8aec1b512a3c8d303c9b` (accessed 2026-10-25).
This is a variant of the STREAM benchmark code. Fetch and build the source in this directory:

```sh
curl -fL https://raw.githubusercontent.com/jeffhammond/STREAM/6703f7504a38a8da96b353cadafa64d3c2d7a2d3/stream.c -o stream.c
echo 'c388924eb140fda95f534cdb808ae7f1f8ebb18da41d8aec1b512a3c8d303c9b  stream.c' | sha256sum -c -
gcc -O3 -fopenmp -DSTREAM_ARRAY_SIZE=80000000 -DNTIMES=20 stream.c -o stream
PATH="$PWD:$PATH" STREAM_THREADS=2 ./run.sh
```

`run.sh` checks the 5.10 output, validation result and emits one methodology
Triad bandwidth record (test `stream-triad`, variant `standard`). Set `STREAM_THREADS` to the requested vCPU count. A captured parser fixture
is in `tests/fixtures/reference-tests/memory.txt`.
