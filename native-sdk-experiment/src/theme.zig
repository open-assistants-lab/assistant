const std = @import("std");
const native_sdk = @import("native_sdk");

const canvas = native_sdk.canvas;
const Color = canvas.Color;

pub const dark_tokens: canvas.DesignTokens = .{
    .colors = .{
        .background = Color.rgb8(8, 9, 12),
        .surface = Color.rgb8(17, 19, 25),
        .surface_subtle = Color.rgb8(14, 16, 21),
        .surface_pressed = Color.rgb8(34, 38, 48),
        .text = Color.rgb8(244, 244, 245),
        .text_muted = Color.rgb8(139, 141, 152),
        .border = Color.rgb8(29, 30, 34),
        .accent = Color.rgb8(20, 184, 166),
        .accent_text = Color.rgb8(4, 47, 46),
        .destructive = Color.rgb8(248, 113, 113),
        .destructive_text = Color.rgb8(26, 10, 10),
        .success = Color.rgb8(74, 222, 128),
        .success_text = Color.rgb8(5, 46, 26),
        .warning = Color.rgb8(251, 191, 36),
        .warning_text = Color.rgb8(26, 22, 6),
        .info = Color.rgb8(96, 165, 250),
        .info_text = Color.rgb8(10, 22, 40),
        .focus_ring = Color.rgb8(20, 184, 166),
        .shadow = Color.rgba8(0, 0, 0, 150),
        .scrim = Color.rgba8(0, 0, 0, 26),
        .disabled = Color.rgb8(58, 61, 68),
    },
    .radius = .{
        .sm = 8,
        .md = 12,
        .lg = 14,
        .xl = 18,
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
        .border = Color.rgb8(229, 231, 235),
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
        .sm = 8,
        .md = 12,
        .lg = 14,
        .xl = 18,
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