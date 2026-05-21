SAFE_METHODS = {1001, 1002, 1036, 1037, 1044, 1045, 1046, 1048, 1051, 2005, 1029, 1042, 1054}
SEMI_SAFE_METHODS = {1021, 1023, 1028, 1030, 1031}
DANGEROUS_METHODS = {1020, 1022, 1026, 1027, 1047, 1038, 2004}


def method_allowed(method: int, allow_commands: bool, allow_dangerous: bool) -> bool:
    if method in SAFE_METHODS:
        return True
    if method in SEMI_SAFE_METHODS:
        return allow_commands
    if method in DANGEROUS_METHODS:
        return allow_commands and allow_dangerous
    return allow_commands and allow_dangerous


def temperature_params(nozzle: int | None, bed: int | None) -> dict:
    payload = {}
    if nozzle is not None:
        payload.update({"nozzle": nozzle, "extruder": nozzle, "target_nozzle": nozzle, "TempTargetNozzle": nozzle})
    if bed is not None:
        payload.update({"bed": bed, "heater_bed": bed, "target_bed": bed, "TempTargetHotbed": bed})
    return payload


def fan_percent_to_pwm(value: int) -> int:
    value = max(0, min(100, int(value)))
    return round(value * 255 / 100)
