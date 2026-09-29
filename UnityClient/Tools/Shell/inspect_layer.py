"""Read the current Unity wallpaper and Explorer icon window state."""
import argparse
import ctypes as c
import ctypes.wintypes as w
import json


parser = argparse.ArgumentParser()
parser.add_argument("pid", type=int)
args = parser.parse_args()
user = c.WinDLL("user32", use_last_error=True)
callback = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
user.EnumWindows.argtypes = [callback, w.LPARAM]
user.EnumChildWindows.argtypes = [w.HWND, callback, w.LPARAM]
user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
user.GetParent.argtypes = [w.HWND]
user.GetParent.restype = w.HWND
user.GetWindow.argtypes = [w.HWND, c.c_uint]
user.GetWindow.restype = w.HWND
user.GetWindowLongW.argtypes = [w.HWND, c.c_int]
user.GetWindowLongW.restype = c.c_long
user.IsWindowVisible.argtypes = [w.HWND]
user.IsWindowVisible.restype = w.BOOL
user.FindWindowExW.argtypes = [w.HWND, w.HWND, w.LPCWSTR, w.LPCWSTR]
user.FindWindowExW.restype = w.HWND
user.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM,
                                     w.UINT, w.UINT, c.POINTER(c.c_size_t)]
user.GetWindowRect.argtypes = [w.HWND, c.POINTER(w.RECT)]


def owner(hwnd):
    pid = w.DWORD()
    user.GetWindowThreadProcessId(hwnd, c.byref(pid))
    return pid.value


def cls(hwnd):
    text = c.create_unicode_buffer(256)
    user.GetClassNameW(hwnd, text, len(text))
    return text.value


found = []


@callback
def visit(hwnd, unused):
    if owner(hwnd) == args.pid and cls(hwnd) == "UnityWndClass":
        found.append(hwnd)
    return True


@callback
def top(hwnd, unused):
    visit(hwnd, unused)
    user.EnumChildWindows(hwnd, visit, 0)
    return True


user.EnumWindows(top, 0)
for hwnd in found:
    rect = w.RECT()
    user.GetWindowRect(hwnd, c.byref(rect))
    parent = user.GetParent(hwnd)
    icon = user.FindWindowExW(parent, None, "SHELLDLL_DefView", None)
    icon_list = user.FindWindowExW(icon, None, "SysListView32", None) if icon else None
    count = c.c_size_t()
    if icon_list:
        user.SendMessageTimeoutW(icon_list, 0x1004, 0, 0, 2, 200, c.byref(count))
    print(json.dumps({
        "hwnd": hwnd,
        "visible": bool(user.IsWindowVisible(hwnd)),
        "style": hex(user.GetWindowLongW(hwnd, -16) & 0xffffffff),
        "rect": [rect.left, rect.top, rect.right, rect.bottom],
        "parent": parent,
        "previous": user.GetWindow(hwnd, 3),
        "icon_view": icon,
        "icon_view_visible": bool(icon and user.IsWindowVisible(icon)),
        "icon_list_visible": bool(icon_list and user.IsWindowVisible(icon_list)),
        "icon_count": count.value,
    }))
