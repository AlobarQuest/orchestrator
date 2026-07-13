from orchestrator.config import ProductionDrillMode, get_settings

PRE_DRILL_REVISION = "0014_wsp21_recovery_controls"
DRILL_REVISION = "0017_runtime_observations"


def production_drill_schema_active(mode: ProductionDrillMode | None = None) -> bool:
    resolved_mode = mode or get_settings().production_drill_mode
    return resolved_mode in {ProductionDrillMode.STANDBY, ProductionDrillMode.ENABLED}


def production_drill_enabled(mode: ProductionDrillMode | None = None) -> bool:
    resolved_mode = mode or get_settings().production_drill_mode
    return production_drill_schema_active(resolved_mode) and (
        resolved_mode is ProductionDrillMode.ENABLED
    )
