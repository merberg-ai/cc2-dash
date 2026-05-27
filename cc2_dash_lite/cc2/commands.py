from __future__ import annotations

from typing import Any, Dict, Optional

# Read-ish methods
GET_ATTRIBUTES = 1001
GET_STATUS = 1002
GET_PRINT_TASK_LIST = 1036
GET_HISTORY_TASK = 1036
GET_HISTORY_TASK_DETAIL = 1037
HISTORY_DELETE = 1038
GET_FILE_LIST = 1044
GET_FILE_THUMBNAIL = 1045
GET_FILE_DETAIL = 1046
GET_DISK_INFO = 1048
GET_TIME_LAPSE_VIDEO_LIST = 1051
GET_CANVAS_STATUS = 2005

# Semi-safe / peripheral methods
SET_LIGHT = 1029
ENABLE_WEBCAM = 1042
START_VIDEO_STREAM = 1054

# Print / printer control methods
START_PRINT = 1020
PAUSE_PRINT = 1021
STOP_PRINT = 1022
RESUME_PRINT = 1023
HOME_AXES = 1026
MOVE_AXES = 1027
SET_TEMPERATURE = 1028
SET_FAN_SPEED = 1030
SET_PRINT_SPEED = 1031
DELETE_FILE = 1047
SET_AUTO_REFILL = 2004

SAFE_METHODS = {
    GET_ATTRIBUTES,
    GET_STATUS,
    GET_PRINT_TASK_LIST,
    GET_HISTORY_TASK,
    GET_HISTORY_TASK_DETAIL,
    GET_FILE_LIST,
    GET_FILE_THUMBNAIL,
    GET_FILE_DETAIL,
    GET_DISK_INFO,
    GET_TIME_LAPSE_VIDEO_LIST,
    GET_CANVAS_STATUS,
    SET_LIGHT,
    ENABLE_WEBCAM,
    START_VIDEO_STREAM,
}

# Things that change state but are not destructive/motion-critical.
SEMI_SAFE_METHODS = {
    PAUSE_PRINT,
    RESUME_PRINT,
    SET_TEMPERATURE,
    SET_FAN_SPEED,
    SET_PRINT_SPEED,
    SET_AUTO_REFILL,
}

DANGEROUS_METHODS = {
    START_PRINT,
    STOP_PRINT,
    HOME_AXES,
    MOVE_AXES,
    DELETE_FILE,
    HISTORY_DELETE,
}


def method_allowed(method: int, allow_commands: bool, allow_dangerous: bool) -> bool:
    if method in SAFE_METHODS:
        return True
    if method in SEMI_SAFE_METHODS:
        return allow_commands
    if method in DANGEROUS_METHODS:
        return allow_commands and allow_dangerous
    return allow_commands and allow_dangerous


def file_list_params(path: str = "/", storage_media: str = "local", page: int = 1, page_size: int = 50, offset: Optional[int] = None, limit: Optional[int] = None) -> Dict[str, Any]:
    # The stock Elegoo portal sends dir/offset/limit. Older cc2-dash builds sent
    # path/page/page_size. Send both shapes to be forgiving across firmware builds.
    if offset is None:
        offset = max(0, (page - 1) * page_size)
    if limit is None:
        limit = page_size
    params: Dict[str, Any] = {
        "storage_media": storage_media,
        "path": path,
        "dir": path,
        "page": page,
        "page_size": page_size,
        "offset": offset,
        "limit": limit,
    }
    if path in ("/", ""):
        params.pop("dir", None)
    return params


def file_detail_params(filename: str, storage_media: str = "local", directory: Optional[str] = None) -> Dict[str, Any]:
    params = {"storage_media": storage_media, "filename": filename}
    if directory:
        params["dir"] = directory
    return params


def file_thumbnail_params(filename: str, storage_media: str = "local") -> Dict[str, Any]:
    return {"storage_media": storage_media, "file_name": filename}


def delete_file_params(file_path: str, storage_media: str = "local") -> Dict[str, Any]:
    return {"storage_media": storage_media, "file_path": file_path}


def start_print_params(
    filename: str,
    storage_media: str = "local",
    start_layer: int = 0,
    calibration: bool = False,
    platform_type: int = 0,
    timelapse: bool = False,
) -> Dict[str, Any]:
    if storage_media == "u-disk" and filename and not filename.startswith("/"):
        filename = "/" + filename
    return {
        "storage_media": storage_media,
        "filename": filename,
        # Compatibility with the SDCP-ish command shape used by the slicer/portal layer.
        "Filename": f"/{storage_media}/{filename.lstrip('/')}" if not filename.startswith(f"/{storage_media}") else filename,
        "StartLayer": int(start_layer or 0),
        "Calibration_switch": 1 if calibration else 0,
        "PrintPlatformType": int(platform_type or 0),
        "Tlp_Switch": 1 if timelapse else 0,
    }


def light_params(on: bool) -> Dict[str, Any]:
    return {"brightness": 255 if on else 0, "power": 1 if on else 0}


def webcam_params(enable: bool) -> Dict[str, Any]:
    return {"enable": bool(enable)}


def pct_to_pwm(value: Any) -> int:
    try:
        v = float(value)
    except Exception:
        v = 0
    v = max(0.0, min(100.0, v))
    return round(v / 100.0 * 255.0)


def fan_params(model: Optional[int] = None, box: Optional[int] = None, aux: Optional[int] = None, values_are_pwm: bool = False) -> Dict[str, Any]:
    def conv(v: Optional[int]) -> Optional[int]:
        if v is None:
            return None
        return int(max(0, min(255, v))) if values_are_pwm else pct_to_pwm(v)

    out: Dict[str, Any] = {}
    model_pwm, box_pwm, aux_pwm = conv(model), conv(box), conv(aux)
    if model_pwm is not None:
        out["fan"] = model_pwm
    if box_pwm is not None:
        out["box_fan"] = box_pwm
    if aux_pwm is not None:
        out["aux_fan"] = aux_pwm
    return out


def temperature_params(nozzle: Optional[int] = None, bed: Optional[int] = None) -> Dict[str, Any]:
    # The stock portal passes a prebuilt object into TemperatureControl. Include
    # the known SDCP-ish names plus the MQTT status object names; firmware should
    # ignore unknown keys, and this gives us a better shot across revisions.
    out: Dict[str, Any] = {}
    if nozzle is not None:
        n = int(nozzle)
        out.update({"nozzle": n, "extruder": n, "target_nozzle": n, "TempTargetNozzle": n})
    if bed is not None:
        b = int(bed)
        out.update({"bed": b, "heater_bed": b, "target_bed": b, "TempTargetHotbed": b})
    return out


def print_speed_params(mode: int) -> Dict[str, Any]:
    return {"mode": int(mode)}


def auto_refill_params(enabled: bool) -> Dict[str, Any]:
    value = 1 if enabled else 0
    # Firmware builds have used slightly different field names for the same
    # switch. Send the known/obvious aliases; the printer ignores unknown keys.
    return {
        "enable": bool(enabled),
        "enabled": bool(enabled),
        "auto_refill": value,
        "autoRefill": value,
        "status": value,
        "switch": value,
    }


def history_detail_params(task_ids: list[str] | list[int] | str | int) -> Dict[str, Any]:
    if not isinstance(task_ids, list):
        task_ids = [task_ids]
    # The stock local-websocket protocol uses {Id:[...]}; some firmware builds
    # also accept lowercase/legacy names. Callers may try alternates when needed.
    return {"Id": task_ids}


def timelapse_export_params(url: str) -> Dict[str, Any]:
    return {"url": url}


def history_delete_params(task_ids: list[str] | list[int]) -> Dict[str, Any]:
    return {"list": task_ids}
