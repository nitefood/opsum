# OPSUM DFU UART Protocol

Host-to-device protocol used to program the main firmware image through the DFU app.

This document is the protocol specification for tool/client and firmware implementation.

## Scope

- Transport: UART on dedicated DFU pins only (device TX=GPIO3, RX=GPIO4).
- Purpose: write and activate the main app partition.
- Integrity model: CRC32 (frame + full-image).
- Authentication/signatures, resume, and DFU self-update are not part of this protocol.

## Frame Format

All multi-byte integer fields are little-endian.

```text
SYNC[2] VER[1] CMD[1] SEQ[2] LEN[2] PAYLOAD[LEN] CRC32[4]
```

- `SYNC`: fixed `0x44 0x46` (`DF`).
- `VER`: protocol version `0x01`.
- `SEQ`: request sequence number from host.
- `LEN`: payload length in bytes.
- `CRC32`: computed over `VER..LEN..PAYLOAD` (sync excluded).

## Limits

- Maximum frame payload (`LEN`): `4104` bytes.
- `DATA` payload header is 8 bytes: `offset[4] + data_len[2] + reserved[2]`.
- Maximum safe `DATA.data_len`: `4096` bytes.

## Commands

| Code | Name | Dir | Purpose |
|---|---|---|---|
| `0x01` | `HELLO` | Host -> DFU | Probe DFU, read capabilities |
| `0x02` | `HELLO_RSP` | DFU -> Host | Version/chip/chunk limits |
| `0x10` | `BEGIN` | Host -> DFU | Start update session with image metadata |
| `0x11` | `BEGIN_RSP` | DFU -> Host | Accept/reject session |
| `0x20` | `DATA` | Host -> DFU | Send one image chunk |
| `0x21` | `DATA_RSP` | DFU -> Host | ACK/NACK + progress |
| `0x30` | `END` | Host -> DFU | Finalize transfer |
| `0x31` | `END_RSP` | DFU -> Host | Commit result |
| `0x40` | `STATUS` | Host -> DFU | Query state/progress/error |
| `0x41` | `STATUS_RSP` | DFU -> Host | State/progress/error reply |
| `0x50` | `ABORT` | Host -> DFU | Cancel active session |
| `0x51` | `ABORT_RSP` | DFU -> Host | Abort acknowledged |
| `0x60` | `REBOOT` | Host -> DFU | Reboot (optional target select) |
| `0x61` | `REBOOT_RSP` | DFU -> Host | Reboot command acknowledged |

## Core Payload Contracts

### `HELLO_RSP`

```c
typedef struct {
  uint8_t  proto_version;
  uint8_t  chip_id;
  uint8_t  flags;
  uint8_t  reserved;
  uint32_t dfu_fw_version;
  uint32_t max_chunk;
  uint32_t app_partition_size;
  uint32_t storage_partition_size;
} hello_rsp_t;
```

Flag behavior:

- bit0 set: storage erase after successful update is enabled.
- bits 1..7: must be `0`.

### `BEGIN`

```c
typedef struct {
  uint32_t total_size;
  uint32_t chunk_size;
  uint32_t expected_crc32;
  uint8_t  target_id;
  uint8_t  options;
  uint16_t reserved;
  uint32_t version_code;
} begin_req_t;
```

Requirements:

- `target_id` must select main app partition.
- `chunk_size <= max_chunk` from `HELLO_RSP`.
- image size must fit app partition.

### `DATA`

```c
typedef struct {
  uint32_t offset;
  uint16_t data_len;
  uint16_t reserved;
  uint8_t  data[];
} data_req_t;
```

Rules:

- First chunk offset is `0`.
- Offsets must be strictly sequential (`next == bytes_written`).
- `data_len` must match actual bytes in `data`.
- Reject overlap, gap, out-of-range, or oversize chunks.

Suggested `DATA_RSP`:

```c
typedef struct {
  uint32_t next_offset;
  uint32_t running_crc32;
  uint16_t status;
  uint16_t reserved;
} data_rsp_t;
```

### `END`

```c
typedef struct {
  uint32_t final_size;
  uint32_t final_crc32;
} end_req_t;
```

`final_size` and `final_crc32` must match the stream actually received.

### `REBOOT`

- `LEN=0`: reboot with current boot target.
- `LEN=1`: one-byte `target_id` selects next boot target before reboot.

Target IDs:

- `0x00`: DFU partition
- `0x01`: main app partition

## Successful Update Sequence

1. Host sends `HELLO`, parses capabilities.
2. Host computes full-image CRC32 and sends `BEGIN`.
3. DFU validates metadata, prepares/erases target app region.
4. Host streams ordered `DATA` chunks.
5. DFU ACKs each chunk (`DATA_RSP`) with `next_offset`.
6. Host sends `END` with final size and CRC32.
7. DFU verifies image, erases `storage`, sets boot target to app, returns `END_RSP`.
8. Host optionally sends `REBOOT`, or DFU reboots by policy.

## Status / Error Codes

| Code | Meaning |
|---|---|
| `0x0000` | OK |
| `0x0001` | Bad frame CRC |
| `0x0002` | Unsupported protocol version |
| `0x0003` | Unknown command |
| `0x0004` | Invalid state |
| `0x0005` | Invalid argument |
| `0x0006` | Oversize image |
| `0x0007` | Partition error |
| `0x0008` | Flash write error |
| `0x0009` | Final CRC mismatch |
| `0x000A` | Sequence mismatch |
| `0x000B` | Offset mismatch |
| `0x000C` | Timeout |
| `0x000D` | Busy |
| `0x000E` | Unsupported target |
| `0x000F` | Image validation failed |
| `0x0010` | Abort completed |

## DFU State Model

Common states exposed by `STATUS_RSP`:

- `IDLE`
- `READY`
- `RECEIVING`
- `VERIFYING`
- `COMMITTED`
- `ERROR`

## Host Requirements

- Increment `SEQ` for each request.
- Always obey `HELLO_RSP.max_chunk`.
- Keep chunk offsets strictly monotonic and contiguous.
- Treat non-OK status as NACK; either retry safely or `ABORT`.
- After reconnect or session loss, restart from `HELLO`.

## Device Behavior Guarantees

- Writes only to the allowed app target partition.
- Enforces partition bounds on every write.
- On successful `END`: verifies image, erases `storage`, switches boot target to app.
- `ABORT` clears in-memory session state.
- Session timeout in `RECEIVING` returns to `IDLE`.

## Interop Note

Normal runtime UART protocol and DFU UART protocol are separate modes.

- Application mode can expose a trigger token (for example `DFU`) to reboot into DFU app.
- Once DFU app is running, UART traffic is DFU binary framing only.
