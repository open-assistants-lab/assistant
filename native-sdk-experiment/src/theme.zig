const std = @import("std");
const native_sdk = @import("native_sdk");

const canvas = native_sdk.canvas;
const Color = canvas.Color;

pub const dark_tokens: canvas.DesignTokens = .{
    .colors = .{
        // openassistants.org tokens.css — exact flat palette. The canvas is
        // PURE #050506 (never tinted): the site's teal "mesh" is a radial
        // glow overlay the native renderer can't draw, so the teal lives in
        // accents + accent-muted surfaces, exactly like the site's glass
        // cards over the black canvas.
        .background = Color.rgb8(5, 5, 6), // #050506
        .surface = Color.rgba8(255, 255, 255, 8), // glass 0.03 (--ea-bg-surface)
        .surface_subtle = Color.rgba8(255, 255, 255, 15), // glass 0.06 (--ea-bg-field)
        .surface_pressed = Color.rgba8(255, 255, 255, 20), // glass 0.08 (hover/press)
        .text = Color.rgb8(244, 244, 245), // --ea-text-primary
        .text_muted = Color.rgb8(161, 161, 170), // --ea-text-secondary #A1A1AA
        .border = Color.rgba8(255, 255, 255, 20), // 0.08 alpha hairline
        .accent = Color.rgb8(20, 184, 166), // --ea-accent #14B8A6
        .accent_text = Color.rgb8(4, 47, 46), // --ea-text-inverse #042F2E
        .destructive = Color.rgb8(248, 113, 113),
        .destructive_text = Color.rgb8(26, 10, 10),
        .success = Color.rgb8(74, 222, 128),
        .success_text = Color.rgb8(5, 46, 26),
        .warning = Color.rgb8(251, 191, 36),
        .warning_text = Color.rgb8(26, 22, 6),
        .info = Color.rgb8(96, 165, 250),
        .info_text = Color.rgb8(10, 22, 40),
        .focus_ring = Color.rgb8(20, 184, 166),
        .shadow = Color.rgba8(0, 0, 0, 128), // --ea-shadow-ambient 0.5
        .scrim = Color.rgba8(0, 0, 0, 26),
        .disabled = Color.rgba8(255, 255, 255, 26),
    },
    .radius = .{
        // Squircle scale from the site tokens (10/14/18/24).
        .sm = 10,
        .md = 14,
        .lg = 18,
        .xl = 24,
    },
    .shadow = .{
        // Diffused ambient + lift (site: 0 24px 64px / 0 12px 32px).
        .sm = .{ .y = 12, .blur = 32, .spread = -12 },
        .md = .{ .y = 24, .blur = 64, .spread = -24 },
    },
    .motion = .{
        .fast_ms = 120,
        .normal_ms = 180,
        .slow_ms = 320,
    },
    .typography = .{
        .font_id = 64, // Geist Regular
        .mono_font_id = 67, // Geist Mono
        .button_font_id = 65, // Geist Medium
    },
};

pub const light_tokens: canvas.DesignTokens = .{
    .colors = .{
        .background = Color.rgb8(255, 255, 255),
        .surface = Color.rgb8(249, 250, 251),
        .surface_subtle = Color.rgb8(243, 244, 246),
        .surface_pressed = Color.rgb8(219, 222, 229),
        .text = Color.rgb8(24, 24, 27),
        .text_muted = Color.rgb8(113, 113, 122),
        .border = Color.rgba8(0, 0, 0, 20), // 0.08 alpha hairline
        .accent = Color.rgb8(13, 148, 136),
        .accent_text = Color.rgb8(255, 255, 255),
        .destructive = Color.rgb8(220, 38, 38),
        .destructive_text = Color.rgb8(255, 255, 255),
        .success = Color.rgb8(22, 163, 74),
        .success_text = Color.rgb8(255, 255, 255),
        .warning = Color.rgb8(217, 119, 6),
        .warning_text = Color.rgb8(255, 255, 255),
        .info = Color.rgb8(37, 99, 235),
        .info_text = Color.rgb8(255, 255, 255),
        .focus_ring = Color.rgb8(13, 148, 136),
        .shadow = Color.rgba8(0, 0, 0, 26),
        .scrim = Color.rgba8(0, 0, 0, 26),
        .disabled = Color.rgb8(212, 212, 216),
    },
    .radius = .{
        .sm = 10,
        .md = 14,
        .lg = 18,
        .xl = 24,
    },
    .shadow = .{
        .sm = .{ .y = 12, .blur = 32, .spread = -12 },
        .md = .{ .y = 24, .blur = 64, .spread = -24 },
    },
    .motion = .{
        .fast_ms = 120,
        .normal_ms = 180,
        .slow_ms = 320,
    },
    .typography = .{
        .font_id = 64, // Geist Regular
        .mono_font_id = 67, // Geist Mono
        .button_font_id = 65, // Geist Medium
    },
};

pub fn darkTokens() canvas.DesignTokens {
    return dark_tokens;
}

pub fn lightTokens() canvas.DesignTokens {
    return light_tokens;
}