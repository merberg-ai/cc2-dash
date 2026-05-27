from __future__ import annotations

THEMES: dict[str, dict] = {
    "octo_dark_blue": {
        "id": "octo_dark_blue",
        "name": "Octo Dark Blue",
        "colors": {
            "bg": "#343941",
            "bg2": "#252932",
            "card": "#292b31",
            "card_soft": "#30333b",
            "border": "rgba(255,255,255,0.09)",
            "text": "#f4f4f5",
            "muted": "#b8bdc8",
            "primary": "#49669c",
            "primary_hover": "#5878b5",
            "success": "#22c55e",
            "warning": "#eab308",
            "danger": "#ef4444",
            "shadow": "rgba(0,0,0,0.35)",
        },
        "fonts": {
            "base": "Terminal Modern",
            "heading": "Terminal Modern",
            "number": "Terminal Modern",
            "button": "Terminal Modern",
        },
        "effects": {
            "glass": False,
            "fade_in": True,
            "scanlines": False,
            "radius": "14px",
            "shadow_strength": "medium",
        },
    },
    "amber_terminal": {
        "id": "amber_terminal",
        "name": "Amber Terminal",
        "colors": {
            "bg": "#12100b",
            "bg2": "#20180e",
            "card": "rgba(29, 22, 14, 0.92)",
            "card_soft": "rgba(45, 33, 18, 0.9)",
            "border": "rgba(255,180,72,0.18)",
            "text": "#ffe7b3",
            "muted": "#c9a96f",
            "primary": "#b46b1d",
            "primary_hover": "#d18228",
            "success": "#55e17a",
            "warning": "#f6be3b",
            "danger": "#ff5a4d",
            "shadow": "rgba(255,154,46,0.10)",
        },
        "fonts": {
            "base": "Terminal Classic",
            "heading": "Sci-Fi Console",
            "number": "Terminal Modern",
            "button": "Sci-Fi Console",
        },
        "effects": {
            "glass": True,
            "fade_in": True,
            "scanlines": True,
            "radius": "12px",
            "shadow_strength": "glow",
        },
    },
    "mainsail_dark": {
        "id": "mainsail_dark",
        "name": "Mainsail-ish Dark",
        "colors": {
            "bg": "#1f2937",
            "bg2": "#111827",
            "card": "#263244",
            "card_soft": "#2e3b4f",
            "border": "rgba(255,255,255,0.08)",
            "text": "#f9fafb",
            "muted": "#cbd5e1",
            "primary": "#2563eb",
            "primary_hover": "#3b82f6",
            "success": "#10b981",
            "warning": "#f59e0b",
            "danger": "#ef4444",
            "shadow": "rgba(0,0,0,0.35)",
        },
        "fonts": {
            "base": "System Sans",
            "heading": "System Sans",
            "number": "Terminal Modern",
            "button": "System Sans",
        },
        "effects": {
            "glass": False,
            "fade_in": True,
            "scanlines": False,
            "radius": "16px",
            "shadow_strength": "medium",
        },
    },
    "carbon_glass": {
        "id": "carbon_glass",
        "name": "Carbon Glass",
        "colors": {
            "bg": "#0f172a",
            "bg2": "#1e293b",
            "card": "rgba(15,23,42,0.72)",
            "card_soft": "rgba(30,41,59,0.76)",
            "border": "rgba(148,163,184,0.18)",
            "text": "#f8fafc",
            "muted": "#cbd5e1",
            "primary": "#475569",
            "primary_hover": "#64748b",
            "success": "#22c55e",
            "warning": "#eab308",
            "danger": "#ef4444",
            "shadow": "rgba(2,6,23,0.55)",
        },
        "fonts": {
            "base": "Industrial Mono",
            "heading": "Industrial Mono",
            "number": "Terminal Modern",
            "button": "Industrial Mono",
        },
        "effects": {
            "glass": True,
            "fade_in": True,
            "scanlines": False,
            "radius": "18px",
            "shadow_strength": "heavy",
        },
    },
    "high_contrast": {
        "id": "high_contrast",
        "name": "High Contrast",
        "colors": {
            "bg": "#000000",
            "bg2": "#111111",
            "card": "#161616",
            "card_soft": "#202020",
            "border": "rgba(255,255,255,0.22)",
            "text": "#ffffff",
            "muted": "#e5e5e5",
            "primary": "#1d4ed8",
            "primary_hover": "#2563eb",
            "success": "#22c55e",
            "warning": "#facc15",
            "danger": "#f87171",
            "shadow": "rgba(0,0,0,0.8)",
        },
        "fonts": {
            "base": "System Sans",
            "heading": "System Sans",
            "number": "Terminal Modern",
            "button": "System Sans",
        },
        "effects": {
            "glass": False,
            "fade_in": False,
            "scanlines": False,
            "radius": "10px",
            "shadow_strength": "none",
        },
    },
}

FONT_STACKS: dict[str, str] = {
    "Terminal Modern": '"JetBrains Mono", "Fira Code", "Cascadia Mono", "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
    "Terminal Classic": '"Share Tech Mono", "VT323", "Courier New", Consolas, "Liberation Mono", monospace',
    "Industrial Mono": '"IBM Plex Mono", "Roboto Mono", "Cascadia Mono", Consolas, "Liberation Mono", monospace',
    "Sci-Fi Console": '"Oxanium", "Share Tech Mono", "JetBrains Mono", Consolas, monospace',
    "Retro CRT": '"VT323", "Courier New", monospace',
    "System Sans": 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
}


def get_theme(theme_id: str | None) -> dict:
    return THEMES.get(theme_id or "octo_dark_blue", THEMES["octo_dark_blue"])


def theme_css_vars(theme_id: str | None, appearance: dict | None = None) -> str:
    theme = get_theme(theme_id)
    appearance = appearance or {}
    colors = theme["colors"]
    fonts = dict(theme.get("fonts", {}))
    overrides = appearance.get("fonts", {}) if isinstance(appearance.get("fonts", {}), dict) else {}
    fonts.update({k: v for k, v in overrides.items() if v})

    def stack(name: str, fallback: str = "Terminal Modern") -> str:
        return FONT_STACKS.get(fonts.get(name, fallback), FONT_STACKS[fallback])

    return "\n".join(
        [
            f"--cc2-{key.replace('_', '-')}: {value};" for key, value in colors.items()
        ]
        + [
            f"--cc2-radius: {theme.get('effects', {}).get('radius', '14px')};",
            f"--font-base: {stack('base')};",
            f"--font-heading: {stack('heading')};",
            f"--font-number: {stack('number')};",
            f"--font-button: {stack('button')};",
        ]
    )
