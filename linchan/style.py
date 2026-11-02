"""Application stylesheet (kept in Python so no data directory is needed at runtime)."""

CSS = """
.post-row { padding: 10px 14px; border-bottom: 1px solid alpha(currentColor, 0.12); }
.post-highlight { background-color: alpha(@accent_bg_color, 0.22); }
.post-thumb { padding: 0; min-width: 0; min-height: 0; border-radius: 6px; }
.post-thumb picture { border-radius: 6px; }
.catalog-card { padding: 8px; }
.catalog-card picture { border-radius: 8px; }
.catalog-grid { padding: 6px; }
window.linchan-viewer { background-color: black; }
.unread-badge { background: @accent_bg_color; color: @accent_fg_color; border-radius: 99px; padding: 0 7px; font-size: smaller; font-weight: bold; }
.dead-thread { opacity: 0.5; }
.preview-card { background-color: @popover_bg_color; color: @popover_fg_color; border-radius: 12px;
                box-shadow: 0 4px 18px rgba(0, 0, 0, 0.45), 0 0 0 1px alpha(currentColor, 0.18); }
.preview-card .post-row { border-bottom: none; }
"""
