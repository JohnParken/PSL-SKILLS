"""Legacy entry retired: WPS-family reading is owned by the wps skill."""


def read_ms_doc(path):
    """Reject the retired API without opening the input."""
    raise ValueError("不支持读取 WPS 格式（.wps/.et/.ett）或旧二进制正文，请使用 wps skill 的 scripts/read_wps.py")
