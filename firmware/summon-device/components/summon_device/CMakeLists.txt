idf_component_register(
    SRCS "src/summon_device.c"
         "src/ws_client.c"
         "src/bridge_codec.c"
         "src/dedup.c"
         "src/lease.c"
         "src/stop_fsm.c"
         "src/heartbeat.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_websocket_client esp_timer esp_event freertos json mbedtls
)
