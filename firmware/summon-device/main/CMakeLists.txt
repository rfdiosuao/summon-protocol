idf_component_register(
    SRCS "port_summon.c"
    INCLUDE_DIRS "."
    REQUIRES summon_device bsp nvs_flash
)
