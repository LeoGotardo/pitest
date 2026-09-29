#!/usr/bin/env python3
"""
screen_test.py — visual screen test for dead pixels and uniformity issues

Each test runs for 3 seconds, then advances automatically.
Animated tests sweep across the full screen during those 3 seconds.
Press ESC or Q to quit.

Run standalone:
    python3 screen_test.py

Run while another task works in the background (see pitest.py):
    from screen_test import run
    run(stop_event=ev, status_fn=lambda: "boot…")

Requires: pip install pygame
"""

import os
import sys

# Evita o spam "ALSA underrun" — esse app não usa áudio.
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
# Double-buffer real no kmsdrm — sem isso o driver mostra só o 1º frame/tela preta.
os.environ.setdefault("SDL_VIDEO_DOUBLE_BUFFER", "1")

try:
    import pygame
except ImportError:
    pygame = None  # importável sem pygame; run()/main() avisam e abortam


# ─── STATIC TESTS ─────────────────────────────────────────────────────────────

def draw_solid(screen, color, label):
    screen.fill(color)
    _draw_label(screen, label)


def draw_checkerboard(screen, _t):
    cell = 32
    W, H = screen.get_size()
    screen.fill((0, 0, 0))
    for row in range(0, H, cell):
        for col in range(0, W, cell):
            if ((row // cell) + (col // cell)) % 2 == 0:
                pygame.draw.rect(screen, (255, 255, 255), (col, row, cell, cell))
    _draw_label(screen, "Checkerboard")


def draw_crosshair(screen, _t):
    W, H = screen.get_size()
    screen.fill((0, 0, 0))
    cx, cy = W // 2, H // 2
    pygame.draw.line(screen, (255, 255, 255), (0, cy), (W, cy), 1)
    pygame.draw.line(screen, (255, 255, 255), (cx, 0), (cx, H), 1)
    size = 40
    for px, py in [(0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1)]:
        pygame.draw.line(screen, (255, 255, 0),
                         (px, py), (px + size * (1 if px == 0 else -1), py), 2)
        pygame.draw.line(screen, (255, 255, 0),
                         (px, py), (px, py + size * (1 if py == 0 else -1)), 2)
    pygame.draw.circle(screen, (255, 255, 255), (cx, cy), min(W, H) // 4, 1)
    _draw_label(screen, "Geometry / Crosshair")


def draw_fine_grid(screen, _t):
    gap = 8
    W, H = screen.get_size()
    screen.fill((0, 0, 0))
    for x in range(0, W, gap):
        pygame.draw.line(screen, (255, 255, 255), (x, 0), (x, H))
    for y in range(0, H, gap):
        pygame.draw.line(screen, (255, 255, 255), (0, y), (W, y))
    _draw_label(screen, "Fine grid (8px)")


# ─── ANIMATED TESTS ───────────────────────────────────────────────────────────

def draw_rgb_bars(screen, t):
    """Color bars scroll left→right: t=0 start, t=1 full sweep done."""
    W, H = screen.get_size()
    colors = [
        (255, 0,   0),
        (0,   255, 0),
        (0,   0,   255),
        (255, 255, 0),
        (0,   255, 255),
        (255, 0,   255),
        (255, 255, 255),
        (128, 128, 128),
        (0,   0,   0),
    ]
    n = len(colors)
    bar_w = W // n
    # offset sweeps one full bar_w per color → total n*bar_w = W pixels in 3s
    offset = int(t * W) % W
    for i, c in enumerate(colors):
        x = (i * bar_w - offset) % W
        # draw two copies to handle wrap-around
        pygame.draw.rect(screen, c, (x, 0, bar_w, H))
        pygame.draw.rect(screen, c, (x - W, 0, bar_w, H))
    _draw_label(screen, "Color bars (scrolling)")


def draw_gradient_h(screen, t):
    """Rainbow gradient scrolls across the screen."""
    W, H = screen.get_size()
    offset = int(t * W)
    for x in range(W):
        tx = ((x + offset) % W) / (W - 1) if W > 1 else 0
        if tx < 0.5:
            r = int(255 * (1 - tx * 2))
            g = int(255 * (tx * 2))
            b = 0
        else:
            r = 0
            g = int(255 * (1 - (tx - 0.5) * 2))
            b = int(255 * ((tx - 0.5) * 2))
        pygame.draw.line(screen, (r, g, b), (x, 0), (x, H))
    _draw_label(screen, "Gradient R→G→B (scrolling)")


def draw_gradient_gray(screen, t):
    """Grayscale ramp scrolls across the screen."""
    W, H = screen.get_size()
    offset = int(t * W)
    for x in range(W):
        v = int(255 * ((x + offset) % W) / (W - 1)) if W > 1 else 0
        pygame.draw.line(screen, (v, v, v), (x, 0), (x, H))
    _draw_label(screen, "Grayscale gradient (scrolling)")


# ─── LABEL ────────────────────────────────────────────────────────────────────

def _draw_label(screen, text):
    W, H = screen.get_size()
    font = pygame.font.SysFont("monospace", 18)
    surf = font.render(text, True, (200, 200, 200))
    bg = pygame.Surface((surf.get_width() + 8, surf.get_height() + 4), pygame.SRCALPHA)
    bg.fill((0, 0, 0, 150))
    screen.blit(bg, (8, H - surf.get_height() - 8))
    screen.blit(surf, (12, H - surf.get_height() - 6))

    hint = font.render("ESC=quit", True, (140, 140, 140))
    bg2 = pygame.Surface((hint.get_width() + 8, hint.get_height() + 4), pygame.SRCALPHA)
    bg2.fill((0, 0, 0, 150))
    screen.blit(bg2, (W - hint.get_width() - 16, H - hint.get_height() - 8))
    screen.blit(hint, (W - hint.get_width() - 12, H - hint.get_height() - 6))


def _draw_status(screen, text):
    """Overlay de uma linha no canto superior esquerdo (progresso de fundo)."""
    if not text:
        return
    font = pygame.font.SysFont("monospace", 16)
    surf = font.render(text, True, (220, 220, 220))
    bg = pygame.Surface((surf.get_width() + 8, surf.get_height() + 4), pygame.SRCALPHA)
    bg.fill((0, 0, 0, 150))
    screen.blit(bg, (8, 8))
    screen.blit(surf, (12, 10))


def _draw_notice(screen, text):
    """Caixa grande centralizada p/ instruções ao operador (ex.: código Bluetooth)."""
    if not text:
        return
    W, H = screen.get_size()
    font = pygame.font.SysFont("monospace", max(24, H // 18), bold=True)
    surf = font.render(text, True, (255, 255, 255))
    if surf.get_width() > W - 40:
        surf = pygame.transform.smoothscale(
            surf, (W - 40, int(surf.get_height() * (W - 40) / surf.get_width())))
    pad = 24
    bg = pygame.Surface((surf.get_width() + 2 * pad, surf.get_height() + 2 * pad), pygame.SRCALPHA)
    bg.fill((0, 0, 0, 220))
    x, y = (W - bg.get_width()) // 2, (H - bg.get_height()) // 2
    screen.blit(bg, (x, y))
    pygame.draw.rect(screen, (255, 255, 0), (x, y, bg.get_width(), bg.get_height()), 3)
    screen.blit(surf, (x + pad, y + pad))


# ─── TEST SEQUENCE ────────────────────────────────────────────────────────────
# Each entry: (label, draw_fn(screen, t), animated)
# animated=True  → redraws every frame, t goes 0.0→1.0 over INTERVAL_MS
# animated=False → draws once, t=0

TESTS = [
    ("Black",        lambda s, t: draw_solid(s, (0, 0, 0),       "Black"),       False),
    ("White",        lambda s, t: draw_solid(s, (255, 255, 255), "White"),       False),
    ("Red",          lambda s, t: draw_solid(s, (255, 0, 0),     "Red"),         False),
    ("Green",        lambda s, t: draw_solid(s, (0, 255, 0),     "Green"),       False),
    ("Blue",         lambda s, t: draw_solid(s, (0, 0, 255),     "Blue"),        False),
    ("Color bars",   draw_rgb_bars,                                               True),
    ("Gradient",     draw_gradient_h,                                             True),
    ("Gray ramp",    draw_gradient_gray,                                          True),
    ("Checkerboard", draw_checkerboard,                                           False),
    ("Fine grid",    draw_fine_grid,                                              False),
    ("Crosshair",    draw_crosshair,                                              False),
]


# ─── RUN LOOP ─────────────────────────────────────────────────────────────────

INTERVAL_MS = 3000


def run(stop_event=None, status_fn=None, notice_fn=None):
    """Roda o loop do teste de tela em fullscreen.

    Sai quando o usuário pressiona ESC/Q/fecha a janela, ou quando
    `stop_event` (threading.Event) é sinalizado por uma tarefa de fundo.

    Args:
      stop_event — Event opcional; quando setado, o loop encerra na próxima
                   iteração (usado pelo pitest.py p/ fechar a tela ao terminar
                   os diagnósticos).
      status_fn  — callable opcional retornando uma string curta; exibida no
                   canto superior esquerdo (ex.: progresso dos diagnósticos).
      notice_fn  — callable opcional retornando uma instrução ao operador;
                   quando não vazia, exibida em destaque no centro da tela.

    Retorna:
      True  — encerrado pelo usuário (ESC/Q/quit)
      False — encerrado por stop_event
    """
    if pygame is None:
        raise RuntimeError("pygame não instalado — pip install pygame")

    try:
        pygame.display.init()
        pygame.font.init()
        pygame.display.set_caption("Screen Test")
        # Falha aqui em ambiente sem display (sem X/Wayland e sem driver KMS/fb).
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        pygame.mouse.set_visible(False)
        clock = pygame.time.Clock()

        idx = 0
        last_switch = pygame.time.get_ticks()
        _, draw_fn, animated = TESTS[idx]

        def _render(t):
            draw_fn(screen, t)
            if status_fn is not None:
                _draw_status(screen, status_fn())
            if notice_fn is not None:
                _draw_notice(screen, notice_fn())
            pygame.display.flip()

        while True:
            if stop_event is not None and stop_event.is_set():
                return False

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return True
                if event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q):
                    return True

            now = pygame.time.get_ticks()
            elapsed = now - last_switch

            if elapsed >= INTERVAL_MS:
                idx = (idx + 1) % len(TESTS)
                _, draw_fn, animated = TESTS[idx]
                last_switch = now
                elapsed = 0

            # Redesenha TODO frame, mesmo em telas estáticas. Em kmsdrm/double-buffer
            # desenhar uma única vez cai num back buffer não exibido → tela preta.
            _render(min(elapsed / INTERVAL_MS, 1.0) if animated else 0.0)
            clock.tick(60)
    finally:
        pygame.quit()


def main():
    if pygame is None:
        sys.exit("Missing: pip install pygame")
    run()


if __name__ == "__main__":
    main()
