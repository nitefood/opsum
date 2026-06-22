#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/rmt_encoder.h"
#include "driver/rmt_tx.h"
#include "driver/uart.h"
#include "esp_crc.h"
#include "esp_err.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_rom_crc.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"

#define DFU_UART_NUM                UART_NUM_1
#define DFU_UART_TX_PIN             GPIO_NUM_3
#define DFU_UART_RX_PIN             GPIO_NUM_4
#define DFU_UART_BAUD               921600

#define DFU_STATUS_LED_GPIO         GPIO_NUM_21
#define DFU_LED_ACTIVE_BLINK_MS     150
#define DFU_LED_BRIGHTNESS          24

#define WS2812_RMT_RES_HZ           10000000UL
#define WS2812_T0H_NS               350UL
#define WS2812_T0L_NS               900UL
#define WS2812_T1H_NS               900UL
#define WS2812_T1L_NS               350UL
#define WS2812_RESET_US             80UL
#define WS2812_BITS_PER_LED         24U
#define WS2812_SYMBOLS_PER_LED      (WS2812_BITS_PER_LED + 1U)

#define DFU_SYNC_0                  0x44U
#define DFU_SYNC_1                  0x46U
#define DFU_PROTO_VER               0x01U

#define DFU_CMD_HELLO               0x01U
#define DFU_CMD_HELLO_RSP           0x02U
#define DFU_CMD_BEGIN               0x10U
#define DFU_CMD_BEGIN_RSP           0x11U
#define DFU_CMD_DATA                0x20U
#define DFU_CMD_DATA_RSP            0x21U
#define DFU_CMD_END                 0x30U
#define DFU_CMD_END_RSP             0x31U
#define DFU_CMD_STATUS              0x40U
#define DFU_CMD_STATUS_RSP          0x41U
#define DFU_CMD_ABORT               0x50U
#define DFU_CMD_ABORT_RSP           0x51U
#define DFU_CMD_REBOOT              0x60U
#define DFU_CMD_REBOOT_RSP          0x61U

#define DFU_STATUS_OK               0x0000U
#define DFU_STATUS_BAD_FRAME_CRC    0x0001U
#define DFU_STATUS_UNSUPPORTED_VER  0x0002U
#define DFU_STATUS_UNKNOWN_CMD      0x0003U
#define DFU_STATUS_INVALID_STATE    0x0004U
#define DFU_STATUS_INVALID_ARG      0x0005U
#define DFU_STATUS_OVERSIZE_IMAGE   0x0006U
#define DFU_STATUS_PARTITION_ERROR  0x0007U
#define DFU_STATUS_FLASH_WRITE_ERR  0x0008U
#define DFU_STATUS_FINAL_CRC_MISM   0x0009U
#define DFU_STATUS_SEQ_MISMATCH     0x000AU
#define DFU_STATUS_OFFSET_MISMATCH  0x000BU
#define DFU_STATUS_TIMEOUT          0x000CU
#define DFU_STATUS_BUSY             0x000DU
#define DFU_STATUS_UNSUP_TARGET     0x000EU
#define DFU_STATUS_IMAGE_INVALID    0x000FU
#define DFU_STATUS_ABORT_DONE       0x0010U

#define DFU_STATE_IDLE              0U
#define DFU_STATE_READY             1U
#define DFU_STATE_RECEIVING         2U
#define DFU_STATE_VERIFYING         3U
#define DFU_STATE_COMMITTED         4U
#define DFU_STATE_ERROR             5U

#define DFU_TARGET_DFU_APP          0x00U
#define DFU_TARGET_MAIN_APP         0x01U
#define DFU_FLAGS_STORAGE_ERASE     0x01U

#define DFU_MAX_CHUNK               4096U
#define DFU_MAX_PAYLOAD             4104U
#define DFU_FRAME_TIMEOUT_MS        2000
#define DFU_SESSION_TIMEOUT_MS      20000

#define ESP_IMAGE_MAGIC             0xE9U

typedef struct {
	uint8_t ver;
	uint8_t cmd;
	uint16_t seq;
	uint16_t len;
	uint8_t payload[DFU_MAX_PAYLOAD];
} dfu_frame_t;

typedef struct __attribute__((packed)) {
	uint8_t proto_version;
	uint8_t chip_id;
	uint8_t flags;
	uint8_t reserved;
	uint32_t dfu_fw_version;
	uint32_t max_chunk;
	uint32_t app_partition_size;
	uint32_t storage_partition_size;
} hello_rsp_t;

typedef struct __attribute__((packed)) {
	uint32_t total_size;
	uint32_t chunk_size;
	uint32_t expected_crc32;
	uint8_t target_id;
	uint8_t options;
	uint16_t reserved;
	uint32_t version_code;
} begin_req_t;

typedef struct __attribute__((packed)) {
	uint32_t offset;
	uint16_t data_len;
	uint16_t reserved;
} data_req_hdr_t;

typedef struct __attribute__((packed)) {
	uint32_t next_offset;
	uint32_t running_crc32;
	uint16_t status;
	uint16_t reserved;
} data_rsp_t;

typedef struct __attribute__((packed)) {
	uint16_t status;
	uint16_t reserved;
} generic_rsp_t;

typedef struct __attribute__((packed)) {
	uint32_t final_size;
	uint32_t final_crc32;
} end_req_t;

typedef struct __attribute__((packed)) {
	uint8_t state;
	uint8_t reserved0;
	uint16_t last_error;
	uint32_t bytes_received;
	uint32_t expected_size;
} status_rsp_t;

static const char *TAG = "dfu";

static const esp_partition_t *s_dfu_part = NULL;
static const esp_partition_t *s_main_app_part = NULL;
static const esp_partition_t *s_storage_part = NULL;

static uint8_t s_state = DFU_STATE_IDLE;
static uint16_t s_last_error = DFU_STATUS_OK;
static uint16_t s_expected_seq = 0;
static int64_t s_last_activity_us = 0;
static int64_t s_last_heartbeat_us = 0;

static rmt_channel_handle_t s_led_rmt_chan = NULL;
static rmt_encoder_handle_t s_led_rmt_encoder = NULL;
static rmt_symbol_word_t s_led_symbols[WS2812_SYMBOLS_PER_LED];
static bool s_led_on = false;
static int64_t s_last_led_toggle_us = 0;

static uint32_t s_expected_size = 0;
static uint32_t s_expected_crc32 = 0;
static uint32_t s_bytes_received = 0;
static uint32_t s_crc_running = 0xFFFFFFFFU;
static bool s_image_header_seen = false;

/* Keep larger protocol buffers in static storage to avoid task-stack pressure. */
static dfu_frame_t s_frame;
static uint8_t s_crc_input_buf[6 + DFU_MAX_PAYLOAD];

static uint16_t le16(const uint8_t *p)
{
	return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t le32(const uint8_t *p)
{
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void wr16(uint8_t *p, uint16_t v)
{
	p[0] = (uint8_t)(v & 0xFFU);
	p[1] = (uint8_t)((v >> 8) & 0xFFU);
}

static void wr32(uint8_t *p, uint32_t v)
{
	p[0] = (uint8_t)(v & 0xFFU);
	p[1] = (uint8_t)((v >> 8) & 0xFFU);
	p[2] = (uint8_t)((v >> 16) & 0xFFU);
	p[3] = (uint8_t)((v >> 24) & 0xFFU);
}

static uint32_t dfu_crc32_calc(const uint8_t *data, size_t len)
{
	uint32_t crc = esp_rom_crc32_le(0xFFFFFFFFU, data, len);
	return crc ^ 0xFFFFFFFFU;
}

static uint32_t dfu_crc32_update(uint32_t running, const uint8_t *data, size_t len)
{
	return esp_rom_crc32_le(running, data, len);
}

static uint32_t dfu_crc32_finalize(uint32_t running)
{
	return running ^ 0xFFFFFFFFU;
}

static uint16_t ws2812_ns_to_ticks(uint32_t ns)
{
	uint64_t ticks = ((uint64_t)WS2812_RMT_RES_HZ * ns + 999999999ULL) / 1000000000ULL;
	if (ticks > 0xFFFFU) {
		ticks = 0xFFFFU;
	}
	return (uint16_t)ticks;
}

static void ws2812_encode_grb(uint8_t g, uint8_t r, uint8_t b)
{
	const uint16_t t0h = ws2812_ns_to_ticks(WS2812_T0H_NS);
	const uint16_t t0l = ws2812_ns_to_ticks(WS2812_T0L_NS);
	const uint16_t t1h = ws2812_ns_to_ticks(WS2812_T1H_NS);
	const uint16_t t1l = ws2812_ns_to_ticks(WS2812_T1L_NS);
	const uint16_t reset_ticks = (uint16_t)((WS2812_RMT_RES_HZ / 1000000UL) * WS2812_RESET_US);
	const uint32_t grb = ((uint32_t)g << 16) | ((uint32_t)r << 8) | (uint32_t)b;

	for (uint32_t i = 0; i < WS2812_BITS_PER_LED; i++) {
		bool one = ((grb >> (WS2812_BITS_PER_LED - 1U - i)) & 0x1U) != 0;
		s_led_symbols[i].level0 = 1;
		s_led_symbols[i].duration0 = one ? t1h : t0h;
		s_led_symbols[i].level1 = 0;
		s_led_symbols[i].duration1 = one ? t1l : t0l;
	}

	s_led_symbols[WS2812_BITS_PER_LED].level0 = 0;
	s_led_symbols[WS2812_BITS_PER_LED].duration0 = reset_ticks / 2U;
	s_led_symbols[WS2812_BITS_PER_LED].level1 = 0;
	s_led_symbols[WS2812_BITS_PER_LED].duration1 = reset_ticks - (reset_ticks / 2U);
}

static void status_led_apply(bool on)
{
	rmt_transmit_config_t tx_cfg = {
		.loop_count = 0,
	};

	if (s_led_rmt_chan == NULL || s_led_rmt_encoder == NULL) {
		return;
	}

	if (on) {
		ws2812_encode_grb(DFU_LED_BRIGHTNESS, DFU_LED_BRIGHTNESS, 0);
	} else {
		ws2812_encode_grb(0, 0, 0);
	}

	if (rmt_transmit(s_led_rmt_chan, s_led_rmt_encoder, s_led_symbols, sizeof(s_led_symbols), &tx_cfg) == ESP_OK) {
		rmt_tx_wait_all_done(s_led_rmt_chan, pdMS_TO_TICKS(20));
	}
}

static void status_led_service(void)
{
	const bool active_transfer = (s_state == DFU_STATE_RECEIVING) || (s_state == DFU_STATE_VERIFYING);
	const bool idle_or_ready = (s_state == DFU_STATE_IDLE) || (s_state == DFU_STATE_READY);
	const int64_t period_us = (int64_t)DFU_LED_ACTIVE_BLINK_MS * 1000;
	int64_t now_us = esp_timer_get_time();

	if (active_transfer) {
		if ((now_us - s_last_led_toggle_us) >= period_us) {
			s_led_on = !s_led_on;
			s_last_led_toggle_us = now_us;
			status_led_apply(s_led_on);
		}
		return;
	}

	if (idle_or_ready) {
		if (!s_led_on) {
			s_led_on = true;
			s_last_led_toggle_us = now_us;
			status_led_apply(true);
		}
		return;
	}

	if (s_led_on) {
		s_led_on = false;
		status_led_apply(false);
	}
}

static const char *state_to_str(uint8_t state)
{
	switch (state) {
		case DFU_STATE_IDLE: return "IDLE";
		case DFU_STATE_READY: return "READY";
		case DFU_STATE_RECEIVING: return "RECEIVING";
		case DFU_STATE_VERIFYING: return "VERIFYING";
		case DFU_STATE_COMMITTED: return "COMMITTED";
		case DFU_STATE_ERROR: return "ERROR";
		default: return "UNKNOWN";
	}
}

static void dfu_heartbeat_log_service(void)
{
	const int64_t period_us = 5000000;
	int64_t now_us = esp_timer_get_time();

	if ((now_us - s_last_heartbeat_us) >= period_us) {
		s_last_heartbeat_us = now_us;
		ESP_LOGW(TAG, "DFU alive: state=%s bytes=%lu/%lu last_err=0x%04X", state_to_str(s_state), (unsigned long)s_bytes_received, (unsigned long)s_expected_size, s_last_error);
	}
}

static uint8_t rsp_cmd_for(uint8_t cmd)
{
	switch (cmd) {
		case DFU_CMD_HELLO: return DFU_CMD_HELLO_RSP;
		case DFU_CMD_BEGIN: return DFU_CMD_BEGIN_RSP;
		case DFU_CMD_DATA: return DFU_CMD_DATA_RSP;
		case DFU_CMD_END: return DFU_CMD_END_RSP;
		case DFU_CMD_STATUS: return DFU_CMD_STATUS_RSP;
		case DFU_CMD_ABORT: return DFU_CMD_ABORT_RSP;
		case DFU_CMD_REBOOT: return DFU_CMD_REBOOT_RSP;
		default: return DFU_CMD_STATUS_RSP;
	}
}

static esp_err_t uart_read_exact(uint8_t *dst, size_t len, uint32_t timeout_ms)
{
	size_t got = 0;
	int64_t start_us = esp_timer_get_time();

	while (got < len) {
		int r = uart_read_bytes(DFU_UART_NUM, dst + got, len - got, pdMS_TO_TICKS(20));
		if (r > 0) {
			got += (size_t)r;
			start_us = esp_timer_get_time();
			continue;
		}

		int64_t now_us = esp_timer_get_time();
		if ((uint64_t)(now_us - start_us) > ((uint64_t)timeout_ms * 1000ULL)) {
			return ESP_ERR_TIMEOUT;
		}
		status_led_service();
		esp_task_wdt_reset();
	}

	return ESP_OK;
}

static esp_err_t dfu_send_frame(uint8_t cmd, uint16_t seq, const uint8_t *payload, uint16_t len)
{
	uint8_t header[8];
	uint8_t crc_bytes[4];

	if (len > DFU_MAX_PAYLOAD) {
		return ESP_ERR_INVALID_SIZE;
	}

	header[0] = DFU_SYNC_0;
	header[1] = DFU_SYNC_1;
	header[2] = DFU_PROTO_VER;
	header[3] = cmd;
	wr16(&header[4], seq);
	wr16(&header[6], len);

	memcpy(s_crc_input_buf, &header[2], 6);
	if (len > 0 && payload != NULL) {
		memcpy(&s_crc_input_buf[6], payload, len);
	}
	wr32(crc_bytes, dfu_crc32_calc(s_crc_input_buf, 6 + len));

	uart_write_bytes(DFU_UART_NUM, (const char *)header, sizeof(header));
	if (len > 0 && payload != NULL) {
		uart_write_bytes(DFU_UART_NUM, (const char *)payload, len);
	}
	uart_write_bytes(DFU_UART_NUM, (const char *)crc_bytes, sizeof(crc_bytes));
	uart_wait_tx_done(DFU_UART_NUM, pdMS_TO_TICKS(50));

	return ESP_OK;
}

static esp_err_t dfu_recv_frame(dfu_frame_t *f)
{
	uint8_t b = 0;
	uint8_t hdr[6];
	uint8_t crc_rx_bytes[4];
	uint32_t crc_rx;
	uint32_t crc_calc;
	bool sync_found = false;

	while (!sync_found) {
		if (uart_read_exact(&b, 1, DFU_FRAME_TIMEOUT_MS) != ESP_OK) {
			return ESP_ERR_TIMEOUT;
		}
		if (b != DFU_SYNC_0) {
			continue;
		}
		if (uart_read_exact(&b, 1, DFU_FRAME_TIMEOUT_MS) != ESP_OK) {
			return ESP_ERR_TIMEOUT;
		}
		if (b == DFU_SYNC_1) {
			sync_found = true;
		}
	}

	if (uart_read_exact(hdr, sizeof(hdr), DFU_FRAME_TIMEOUT_MS) != ESP_OK) {
		return ESP_ERR_TIMEOUT;
	}

	f->ver = hdr[0];
	f->cmd = hdr[1];
	f->seq = le16(&hdr[2]);
	f->len = le16(&hdr[4]);

	if (f->len > DFU_MAX_PAYLOAD) {
		return ESP_ERR_INVALID_SIZE;
	}

	if (f->len > 0) {
		if (uart_read_exact(f->payload, f->len, DFU_FRAME_TIMEOUT_MS) != ESP_OK) {
			return ESP_ERR_TIMEOUT;
		}
	}

	if (uart_read_exact(crc_rx_bytes, sizeof(crc_rx_bytes), DFU_FRAME_TIMEOUT_MS) != ESP_OK) {
		return ESP_ERR_TIMEOUT;
	}

	memcpy(s_crc_input_buf, hdr, sizeof(hdr));
	if (f->len > 0) {
		memcpy(&s_crc_input_buf[sizeof(hdr)], f->payload, f->len);
	}
	crc_calc = dfu_crc32_calc(s_crc_input_buf, sizeof(hdr) + f->len);
	crc_rx = le32(crc_rx_bytes);

	if (crc_calc != crc_rx) {
		return ESP_ERR_INVALID_CRC;
	}

	return ESP_OK;
}

static void reset_session(void)
{
	s_expected_size = 0;
	s_expected_crc32 = 0;
	s_bytes_received = 0;
	s_crc_running = 0xFFFFFFFFU;
	s_image_header_seen = false;
}

static esp_err_t erase_partition_full(const esp_partition_t *p)
{
	if (p == NULL) {
		return ESP_ERR_INVALID_ARG;
	}
	return esp_partition_erase_range(p, 0, p->size);
}

static void send_generic_status_rsp(uint8_t req_cmd, uint16_t req_seq, uint16_t status)
{
	generic_rsp_t rsp = {
		.status = status,
		.reserved = 0,
	};
	dfu_send_frame(rsp_cmd_for(req_cmd), req_seq, (const uint8_t *)&rsp, sizeof(rsp));
}

static void handle_hello(const dfu_frame_t *f)
{
	hello_rsp_t rsp = {
		.proto_version = DFU_PROTO_VER,
		.chip_id = 0x09,
		.flags = DFU_FLAGS_STORAGE_ERASE,
		.reserved = 0,
		.dfu_fw_version = 0x00010000,
		.max_chunk = DFU_MAX_CHUNK,
		.app_partition_size = s_main_app_part ? s_main_app_part->size : 0,
		.storage_partition_size = s_storage_part ? s_storage_part->size : 0,
	};

	s_state = DFU_STATE_READY;
	s_last_error = DFU_STATUS_OK;
	dfu_send_frame(DFU_CMD_HELLO_RSP, f->seq, (const uint8_t *)&rsp, sizeof(rsp));
}

static void handle_begin(const dfu_frame_t *f)
{
	begin_req_t req;

	if (f->len != sizeof(begin_req_t)) {
		send_generic_status_rsp(DFU_CMD_BEGIN, f->seq, DFU_STATUS_INVALID_ARG);
		return;
	}
	if (s_state == DFU_STATE_RECEIVING || s_state == DFU_STATE_VERIFYING) {
		send_generic_status_rsp(DFU_CMD_BEGIN, f->seq, DFU_STATUS_BUSY);
		return;
	}
	if (s_main_app_part == NULL || s_storage_part == NULL) {
		send_generic_status_rsp(DFU_CMD_BEGIN, f->seq, DFU_STATUS_PARTITION_ERROR);
		return;
	}

	memcpy(&req, f->payload, sizeof(req));

	if (req.target_id != DFU_TARGET_MAIN_APP) {
		send_generic_status_rsp(DFU_CMD_BEGIN, f->seq, DFU_STATUS_UNSUP_TARGET);
		return;
	}
	if (req.total_size == 0 || req.total_size > s_main_app_part->size) {
		send_generic_status_rsp(DFU_CMD_BEGIN, f->seq, DFU_STATUS_OVERSIZE_IMAGE);
		return;
	}
	if (req.chunk_size == 0 || req.chunk_size > DFU_MAX_CHUNK) {
		send_generic_status_rsp(DFU_CMD_BEGIN, f->seq, DFU_STATUS_INVALID_ARG);
		return;
	}

	if (erase_partition_full(s_main_app_part) != ESP_OK) {
		send_generic_status_rsp(DFU_CMD_BEGIN, f->seq, DFU_STATUS_PARTITION_ERROR);
		return;
	}

	reset_session();
	s_expected_size = req.total_size;
	s_expected_crc32 = req.expected_crc32;
	s_state = DFU_STATE_RECEIVING;
	s_last_error = DFU_STATUS_OK;

	send_generic_status_rsp(DFU_CMD_BEGIN, f->seq, DFU_STATUS_OK);
}

static void handle_data(const dfu_frame_t *f)
{
	data_req_hdr_t hdr;
	data_rsp_t rsp;
	const uint8_t *chunk;
	esp_err_t err;

	rsp.next_offset = s_bytes_received;
	rsp.running_crc32 = dfu_crc32_finalize(s_crc_running);
	rsp.status = DFU_STATUS_OK;
	rsp.reserved = 0;

	if (s_state != DFU_STATE_RECEIVING) {
		rsp.status = DFU_STATUS_INVALID_STATE;
		dfu_send_frame(DFU_CMD_DATA_RSP, f->seq, (const uint8_t *)&rsp, sizeof(rsp));
		return;
	}
	if (f->len < sizeof(data_req_hdr_t)) {
		rsp.status = DFU_STATUS_INVALID_ARG;
		dfu_send_frame(DFU_CMD_DATA_RSP, f->seq, (const uint8_t *)&rsp, sizeof(rsp));
		return;
	}

	memcpy(&hdr, f->payload, sizeof(hdr));
	chunk = &f->payload[sizeof(hdr)];

	if ((uint16_t)(f->len - sizeof(hdr)) != hdr.data_len) {
		rsp.status = DFU_STATUS_INVALID_ARG;
		dfu_send_frame(DFU_CMD_DATA_RSP, f->seq, (const uint8_t *)&rsp, sizeof(rsp));
		return;
	}
	if (hdr.offset != s_bytes_received) {
		rsp.status = DFU_STATUS_OFFSET_MISMATCH;
		dfu_send_frame(DFU_CMD_DATA_RSP, f->seq, (const uint8_t *)&rsp, sizeof(rsp));
		return;
	}
	if (((uint64_t)s_bytes_received + hdr.data_len) > s_expected_size) {
		rsp.status = DFU_STATUS_OVERSIZE_IMAGE;
		dfu_send_frame(DFU_CMD_DATA_RSP, f->seq, (const uint8_t *)&rsp, sizeof(rsp));
		return;
	}

	if (!s_image_header_seen && hdr.offset == 0 && hdr.data_len > 0 && chunk[0] == ESP_IMAGE_MAGIC) {
		s_image_header_seen = true;
	}

	err = esp_partition_write(s_main_app_part, hdr.offset, chunk, hdr.data_len);
	if (err != ESP_OK) {
		rsp.status = DFU_STATUS_FLASH_WRITE_ERR;
		dfu_send_frame(DFU_CMD_DATA_RSP, f->seq, (const uint8_t *)&rsp, sizeof(rsp));
		return;
	}

	s_crc_running = dfu_crc32_update(s_crc_running, chunk, hdr.data_len);
	s_bytes_received += hdr.data_len;

	rsp.next_offset = s_bytes_received;
	rsp.running_crc32 = dfu_crc32_finalize(s_crc_running);
	rsp.status = DFU_STATUS_OK;

	dfu_send_frame(DFU_CMD_DATA_RSP, f->seq, (const uint8_t *)&rsp, sizeof(rsp));
}

static void handle_end(const dfu_frame_t *f)
{
	end_req_t req;
	uint32_t final_crc32;

	if (s_state != DFU_STATE_RECEIVING) {
		send_generic_status_rsp(DFU_CMD_END, f->seq, DFU_STATUS_INVALID_STATE);
		return;
	}
	if (f->len != sizeof(end_req_t)) {
		send_generic_status_rsp(DFU_CMD_END, f->seq, DFU_STATUS_INVALID_ARG);
		return;
	}

	memcpy(&req, f->payload, sizeof(req));

	s_state = DFU_STATE_VERIFYING;

	final_crc32 = dfu_crc32_finalize(s_crc_running);

	if (!s_image_header_seen) {
		s_state = DFU_STATE_ERROR;
		s_last_error = DFU_STATUS_IMAGE_INVALID;
		send_generic_status_rsp(DFU_CMD_END, f->seq, DFU_STATUS_IMAGE_INVALID);
		return;
	}
	if (req.final_size != s_bytes_received || req.final_size != s_expected_size) {
		s_state = DFU_STATE_ERROR;
		s_last_error = DFU_STATUS_INVALID_ARG;
		send_generic_status_rsp(DFU_CMD_END, f->seq, DFU_STATUS_INVALID_ARG);
		return;
	}
	if (req.final_crc32 != final_crc32 || req.final_crc32 != s_expected_crc32) {
		s_state = DFU_STATE_ERROR;
		s_last_error = DFU_STATUS_FINAL_CRC_MISM;
		send_generic_status_rsp(DFU_CMD_END, f->seq, DFU_STATUS_FINAL_CRC_MISM);
		return;
	}
	if (erase_partition_full(s_storage_part) != ESP_OK) {
		s_state = DFU_STATE_ERROR;
		s_last_error = DFU_STATUS_PARTITION_ERROR;
		send_generic_status_rsp(DFU_CMD_END, f->seq, DFU_STATUS_PARTITION_ERROR);
		return;
	}
	if (esp_ota_set_boot_partition(s_main_app_part) != ESP_OK) {
		s_state = DFU_STATE_ERROR;
		s_last_error = DFU_STATUS_IMAGE_INVALID;
		send_generic_status_rsp(DFU_CMD_END, f->seq, DFU_STATUS_IMAGE_INVALID);
		return;
	}

	s_state = DFU_STATE_COMMITTED;
	s_last_error = DFU_STATUS_OK;
	send_generic_status_rsp(DFU_CMD_END, f->seq, DFU_STATUS_OK);
}

static void handle_status(const dfu_frame_t *f)
{
	status_rsp_t rsp = {
		.state = s_state,
		.reserved0 = 0,
		.last_error = s_last_error,
		.bytes_received = s_bytes_received,
		.expected_size = s_expected_size,
	};
	dfu_send_frame(DFU_CMD_STATUS_RSP, f->seq, (const uint8_t *)&rsp, sizeof(rsp));
}

static void handle_abort(const dfu_frame_t *f)
{
	/* Reset sequence so fresh host sessions can start from seq=0 after ABORT. */
	s_expected_seq = 0;
	reset_session();
	s_state = DFU_STATE_IDLE;
	s_last_error = DFU_STATUS_ABORT_DONE;
	send_generic_status_rsp(DFU_CMD_ABORT, f->seq, DFU_STATUS_ABORT_DONE);
}

static void handle_reboot(const dfu_frame_t *f)
{
	esp_err_t err;

	if (f->len > 1) {
		send_generic_status_rsp(DFU_CMD_REBOOT, f->seq, DFU_STATUS_INVALID_ARG);
		return;
	}

	if (f->len == 1) {
		if (f->payload[0] == DFU_TARGET_MAIN_APP) {
			if (s_main_app_part == NULL) {
				send_generic_status_rsp(DFU_CMD_REBOOT, f->seq, DFU_STATUS_PARTITION_ERROR);
				return;
			}
			err = esp_ota_set_boot_partition(s_main_app_part);
			if (err != ESP_OK) {
				send_generic_status_rsp(DFU_CMD_REBOOT, f->seq, DFU_STATUS_PARTITION_ERROR);
				return;
			}
		} else if (f->payload[0] == DFU_TARGET_DFU_APP) {
			if (s_dfu_part == NULL) {
				send_generic_status_rsp(DFU_CMD_REBOOT, f->seq, DFU_STATUS_PARTITION_ERROR);
				return;
			}
			err = esp_ota_set_boot_partition(s_dfu_part);
			if (err != ESP_OK) {
				send_generic_status_rsp(DFU_CMD_REBOOT, f->seq, DFU_STATUS_PARTITION_ERROR);
				return;
			}
		} else {
			send_generic_status_rsp(DFU_CMD_REBOOT, f->seq, DFU_STATUS_UNSUP_TARGET);
			return;
		}
	}

	s_last_error = DFU_STATUS_OK;
	send_generic_status_rsp(DFU_CMD_REBOOT, f->seq, DFU_STATUS_OK);
	vTaskDelay(pdMS_TO_TICKS(100));
	esp_restart();
}

static void process_frame(const dfu_frame_t *f)
{
	if (f->ver != DFU_PROTO_VER) {
		s_last_error = DFU_STATUS_UNSUPPORTED_VER;
		send_generic_status_rsp(f->cmd, f->seq, DFU_STATUS_UNSUPPORTED_VER);
		return;
	}

	if (f->seq != s_expected_seq) {
		s_last_error = DFU_STATUS_SEQ_MISMATCH;
		send_generic_status_rsp(f->cmd, f->seq, DFU_STATUS_SEQ_MISMATCH);
		return;
	}

	switch (f->cmd) {
		case DFU_CMD_HELLO:
			handle_hello(f);
			break;
		case DFU_CMD_BEGIN:
			handle_begin(f);
			break;
		case DFU_CMD_DATA:
			handle_data(f);
			break;
		case DFU_CMD_END:
			handle_end(f);
			break;
		case DFU_CMD_STATUS:
			handle_status(f);
			break;
		case DFU_CMD_ABORT:
			handle_abort(f);
			break;
		case DFU_CMD_REBOOT:
			handle_reboot(f);
			break;
		default:
			s_last_error = DFU_STATUS_UNKNOWN_CMD;
			send_generic_status_rsp(f->cmd, f->seq, DFU_STATUS_UNKNOWN_CMD);
			break;
	}

	if (f->cmd != DFU_CMD_ABORT) {
		s_expected_seq++;
	}
}

static esp_err_t dfu_init_partitions(void)
{
	s_dfu_part = esp_partition_find_first(ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_ANY, "dfu");
	s_main_app_part = esp_partition_find_first(ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_ANY, "app");
	s_storage_part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "storage");

	if (s_dfu_part == NULL || s_main_app_part == NULL || s_storage_part == NULL) {
		return ESP_ERR_NOT_FOUND;
	}

	return ESP_OK;
}

static esp_err_t dfu_init_uart(void)
{
	const uart_config_t uart_cfg = {
		.baud_rate = DFU_UART_BAUD,
		.data_bits = UART_DATA_8_BITS,
		.parity = UART_PARITY_DISABLE,
		.stop_bits = UART_STOP_BITS_1,
		.flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
		.source_clk = UART_SCLK_DEFAULT,
	};

	ESP_ERROR_CHECK(uart_driver_install(DFU_UART_NUM, 4096, 0, 0, NULL, 0));
	ESP_ERROR_CHECK(uart_param_config(DFU_UART_NUM, &uart_cfg));
	ESP_ERROR_CHECK(uart_set_pin(DFU_UART_NUM, DFU_UART_TX_PIN, DFU_UART_RX_PIN, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
	ESP_ERROR_CHECK(uart_flush(DFU_UART_NUM));

	return ESP_OK;
}

static void dfu_init_wdt(void)
{
    esp_err_t err;
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
	const esp_task_wdt_config_t cfg = {
		.timeout_ms = 5000,
		.idle_core_mask = 0,
		.trigger_panic = true,
	};
	err = esp_task_wdt_init(&cfg);
#else
	err = esp_task_wdt_init(5, true);
#endif
	if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
		ESP_ERROR_CHECK(err);
	}

	err = esp_task_wdt_add(NULL);
	if (err != ESP_OK && err != ESP_ERR_INVALID_ARG) {
		ESP_ERROR_CHECK(err);
	}
}

static esp_err_t dfu_init_status_led(void)
{
	rmt_tx_channel_config_t tx_chan_cfg = {
		.gpio_num = DFU_STATUS_LED_GPIO,
		.clk_src = RMT_CLK_SRC_DEFAULT,
		.resolution_hz = WS2812_RMT_RES_HZ,
		.mem_block_symbols = 64,
		.trans_queue_depth = 4,
		.flags = {
			.invert_out = false,
			.with_dma = false,
		},
	};
	rmt_copy_encoder_config_t copy_encoder_cfg = {};

	ESP_RETURN_ON_ERROR(rmt_new_tx_channel(&tx_chan_cfg, &s_led_rmt_chan), TAG, "Failed to create RMT channel");
	ESP_RETURN_ON_ERROR(rmt_new_copy_encoder(&copy_encoder_cfg, &s_led_rmt_encoder), TAG, "Failed to create RMT encoder");
	ESP_RETURN_ON_ERROR(rmt_enable(s_led_rmt_chan), TAG, "Failed to enable RMT channel");

	s_led_on = false;
	s_last_led_toggle_us = esp_timer_get_time();
	status_led_apply(false);

	return ESP_OK;
}

void app_main(void)
{
	esp_err_t err;

	esp_log_level_set("*", ESP_LOG_WARN);

	dfu_init_wdt();
	ESP_ERROR_CHECK(dfu_init_partitions());
	ESP_ERROR_CHECK(dfu_init_uart());
	ESP_ERROR_CHECK(dfu_init_status_led());

	reset_session();
	s_state = DFU_STATE_IDLE;
	s_last_error = DFU_STATUS_OK;
	s_expected_seq = 0;
	s_last_activity_us = esp_timer_get_time();
	s_last_heartbeat_us = 0;

	ESP_LOGW(TAG, "DFU booted on UART1 TX=GPIO3 RX=GPIO4, status NeoPixel GPIO21");

	while (true) {
		status_led_service();
		dfu_heartbeat_log_service();
		err = dfu_recv_frame(&s_frame);

		if (err == ESP_ERR_TIMEOUT) {
			if (s_state == DFU_STATE_RECEIVING) {
				int64_t now_us = esp_timer_get_time();
				if ((uint64_t)(now_us - s_last_activity_us) > ((uint64_t)DFU_SESSION_TIMEOUT_MS * 1000ULL)) {
					s_expected_seq = 0;
					reset_session();
					s_state = DFU_STATE_IDLE;
					s_last_error = DFU_STATUS_TIMEOUT;
				}
			}
			esp_task_wdt_reset();
			continue;
		}

		if (err == ESP_ERR_INVALID_CRC) {
			s_last_error = DFU_STATUS_BAD_FRAME_CRC;
			s_state = (s_state == DFU_STATE_RECEIVING) ? DFU_STATE_RECEIVING : s_state;
			esp_task_wdt_reset();
			continue;
		}

		if (err != ESP_OK) {
			s_last_error = DFU_STATUS_INVALID_ARG;
			esp_task_wdt_reset();
			continue;
		}

		s_last_activity_us = esp_timer_get_time();
		process_frame(&s_frame);
		esp_task_wdt_reset();
	}
}