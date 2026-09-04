"""
Sprach-Assistent – Phase 4: Fenster + Startpunkt.

Zeigt: Status-Punkt (grau/grün/rot), Gesprächsverlauf als Text, Mute-Knopf,
Einstellungen (Stimme, Duo-Mode), Sitzungen speichern/öffnen, Rollenwechsel
(Duo-Mode manuell) und den still mitlaufenden Hintergrund-Prüfer.
Start: python main.py
"""
import asyncio

import flet as ft
from google import genai

import background_critic
import code_context
import duo_mode
from audio_engine import AudioEngine, list_audio_devices
from background_critic import BackgroundCritic
from config import AppConfig
from gemini_session import GeminiLiveSession
from sessions import list_sessions, load_session, save_session

STATUS_COLORS = {
    "idle": ft.Colors.GREY_500,
    "connecting": ft.Colors.AMBER_600,
    "connected": ft.Colors.GREEN_500,
    "error": ft.Colors.RED_500,
}

DUO_MODE_LABELS = {
    "off": "Aus",
    "auto": "Automatisch (nach jeder Antwort)",
    "manual": "Manuell (per Knopf)",
}

# Bekannte Gemini-Live-Stimmen (ca. 30 zur Auswahl).
KNOWN_VOICES = [
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
]


async def main(page: ft.Page):
    page.title = "Sprach-Assistent – Phase 4"
    page.window.width = 520
    page.window.height = 720
    page.theme_mode = ft.ThemeMode.DARK

    # --- UI-Elemente ---
    status_dot = ft.Container(
        width=14,
        height=14,
        border_radius=7,
        bgcolor=STATUS_COLORS["idle"],
    )
    status_text = ft.Text("Bereit – verbinde gleich …", size=14)
    settings_button = ft.IconButton(icon=ft.Icons.SETTINGS, tooltip="Einstellungen")
    status_row = ft.Row(
        [status_dot, status_text, ft.Container(expand=True), settings_button],
        alignment=ft.MainAxisAlignment.CENTER,
    )
    role_text = ft.Text("", size=12, italic=True, visible=False)

    transcript_view = ft.ListView(expand=True, spacing=6, auto_scroll=True)

    mute_button = ft.FilledButton(
        "Mikro stummschalten",
        icon=ft.Icons.MIC,
    )
    save_session_button = ft.OutlinedButton(
        "Sitzung speichern", icon=ft.Icons.SAVE
    )
    open_sessions_button = ft.OutlinedButton(
        "Sitzungen öffnen", icon=ft.Icons.FOLDER_OPEN
    )
    switch_role_button = ft.OutlinedButton(
        "Rolle wechseln", icon=ft.Icons.SWAP_HORIZ, visible=False
    )
    send_context_button = ft.OutlinedButton(
        "Code-Kontext senden", icon=ft.Icons.CONTENT_PASTE
    )

    page.add(
        ft.Container(status_row, padding=12),
        ft.Container(role_text, padding=ft.Padding(12, 0, 12, 0)),
        ft.Divider(height=1),
        ft.Container(transcript_view, expand=True, padding=12),
        ft.Container(
            ft.Row(
                [
                    mute_button,
                    save_session_button,
                    open_sessions_button,
                    switch_role_button,
                    send_context_button,
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                wrap=True,
            ),
            padding=12,
        ),
    )

    # --- Helfer ---
    def set_status(text: str, kind: str) -> None:
        status_text.value = text
        status_dot.bgcolor = STATUS_COLORS.get(kind, STATUS_COLORS["idle"])
        page.update()

    transcript_log: list[dict] = []
    critic: BackgroundCritic | None = None  # nach dem Config-Laden gesetzt

    def add_transcript_line(who: str, text: str) -> None:
        transcript_log.append({"who": who, "text": text})
        is_user = who == "Du"
        transcript_view.controls.append(
            ft.Row(
                [
                    ft.Container(
                        content=ft.Text(f"{who}: {text}", size=13),
                        bgcolor=ft.Colors.BLUE_GREY_800
                        if is_user
                        else ft.Colors.TEAL_900,
                        padding=8,
                        border_radius=8,
                    )
                ],
                alignment=ft.MainAxisAlignment.END
                if is_user
                else ft.MainAxisAlignment.START,
            )
        )
        page.update()
        if critic is not None and critic.register_turn(who):
            asyncio.create_task(run_critic_check())

    async def run_critic_check() -> None:
        """Phase 4: fragt den Hintergrund-Prüfer, flüstert der Live-Sitzung einen
        Hinweis zu, falls er einen Widerspruch/Logikfehler findet. Netzwerkfehler
        hier dürfen die laufende Sprach-Sitzung nie stören (siehe BackgroundCritic.check)."""
        hint = await critic.check(transcript_log)
        if hint:
            await session.send_text(background_critic.wrap_hint(hint))

    # --- Einstellungen (Stimme, Duo-Mode) ---
    def open_settings(_event) -> None:
        current_voice = config.voice
        options = KNOWN_VOICES if current_voice in KNOWN_VOICES else [current_voice, *KNOWN_VOICES]
        voice_dropdown = ft.Dropdown(
            label="Stimme",
            value=current_voice,
            options=[ft.dropdown.Option(v) for v in options],
        )
        duo_dropdown = ft.Dropdown(
            label="Duo-Mode (zwei Denkrollen)",
            value=config.duo_mode,
            options=[
                ft.dropdown.Option(key=mode, text=label)
                for mode, label in DUO_MODE_LABELS.items()
            ],
        )
        hint = ft.Text(
            "Wirkt erst beim nächsten Start der App (Stimme + Duo-Mode werden nur "
            "beim Verbinden gesetzt).",
            size=11,
            italic=True,
        )

        def close_dialog(_e=None) -> None:
            page.pop_dialog()

        def save_settings(_e) -> None:
            AppConfig.save_settings(voice=voice_dropdown.value, duo_mode=duo_dropdown.value)
            config.voice = voice_dropdown.value
            config.duo_mode = duo_dropdown.value
            close_dialog()
            set_status(
                "Einstellungen gespeichert – wirkt nach Neustart.",
                "connected" if status_text.value.startswith("Verbunden") else "idle",
            )

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Einstellungen"),
            content=ft.Column([voice_dropdown, duo_dropdown, hint], tight=True, spacing=8),
            actions=[
                ft.TextButton("Abbrechen", on_click=close_dialog),
                ft.FilledButton("Speichern", on_click=save_settings),
            ],
        )
        page.show_dialog(dialog)

    settings_button.on_click = open_settings

    # --- Sitzungen speichern / öffnen ---
    def do_save_session(_event) -> None:
        if not transcript_log:
            set_status("Nichts zu speichern – noch kein Gespräch geführt.", "idle")
            return
        path = save_session(transcript_log)
        set_status(f"Sitzung gespeichert: {path.name}", "connected" if status_text.value.startswith("Verbunden") else "idle")

    save_session_button.on_click = do_save_session

    def open_sessions(_event) -> None:
        found = list_sessions()

        def close_dialog(_e=None) -> None:
            page.pop_dialog()

        def show_session(path):
            def _open(_e) -> None:
                records = load_session(path)
                content = ft.ListView(
                    [
                        ft.Text(f"{r.get('who', '?')}: {r.get('text', '')}", size=13)
                        for r in records
                    ],
                    spacing=6,
                    height=400,
                )
                view_dialog = ft.AlertDialog(
                    modal=True,
                    title=ft.Text(path.stem),
                    content=content,
                    actions=[ft.TextButton("Schließen", on_click=close_dialog)],
                )
                page.show_dialog(view_dialog)

            return _open

        if not found:
            body = ft.Text("Noch keine gespeicherten Sitzungen.")
        else:
            body = ft.ListView(
                [
                    ft.ListTile(
                        title=ft.Text(p.stem),
                        leading=ft.Icon(ft.Icons.CHAT),
                        on_click=show_session(p),
                    )
                    for p in found
                ],
                height=400,
            )

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Gespeicherte Sitzungen"),
            content=body,
            actions=[ft.TextButton("Schließen", on_click=close_dialog)],
        )
        page.show_dialog(dialog)

    open_sessions_button.on_click = open_sessions

    # --- Aufbau ---
    try:
        config = AppConfig.load()
    except SystemExit as exc:
        set_status(str(exc), "error")
        return

    # Phase 3: Duo-Mode-Regeln in die System-Instruktion einbauen (kein
    # Reconnect für Rollenwechsel möglich, siehe README Entscheidung 1).
    config.system_instruction = duo_mode.build_system_instruction(
        config.system_instruction, config.duo_mode
    )
    current_role = duo_mode.ROLE_VISIONAER
    if config.duo_mode == "manual":
        switch_role_button.visible = True
        role_text.visible = True
        role_text.value = f"Aktuelle Rolle: {current_role}"
        page.update()

    # Phase 4: Hintergrund-Prüfer (separater, nicht-live Text-Aufruf).
    if config.critic_enabled:
        critic_client = genai.Client(api_key=config.api_key)
        critic = BackgroundCritic(
            critic_client, config.critic_model, config.critic_check_every
        )

    # Zur Fehlersuche: alle Ton-Geräte ins Terminal schreiben
    print("=== Gefundene Ton-Geräte ===")
    print(list_audio_devices())
    print("============================")

    loop = asyncio.get_running_loop()

    audio = AudioEngine(
        loop,
        input_device=config.input_device,
        output_device=config.output_device,
    )
    session = GeminiLiveSession(
        config=config,
        audio=audio,
        on_status=lambda s: set_status(
            s, "connected" if s.startswith("Verbunden") else "connecting"
        ),
        on_transcript=add_transcript_line,
    )

    def do_switch_role(_event) -> None:
        nonlocal current_role
        current_role = duo_mode.next_role(current_role)
        asyncio.create_task(session.send_text(duo_mode.build_switch_message(current_role)))
        role_text.value = f"Aktuelle Rolle: {current_role}"
        page.update()

    switch_role_button.on_click = do_switch_role

    def do_send_context(_event) -> None:
        """10x-Feature: schickt den aktuellen Zwischenablage-Inhalt (Code-Ausschnitt)
        unsichtbar in die laufende Sitzung, damit die KI real vorliegenden Code
        kennt statt nur die gesprochene Beschreibung davon."""
        if session.session is None:
            set_status("Nicht verbunden – Code-Kontext kann erst nach dem Verbinden gesendet werden.", "idle")
            return
        clipboard_text = code_context.read_clipboard()
        message = code_context.build_context_message(clipboard_text)
        if message is None:
            set_status("Zwischenablage leer oder nicht lesbar – kein Kontext gesendet.", "connected")
            return
        asyncio.create_task(session.send_text(message))
        set_status("Code-Kontext aus Zwischenablage gesendet.", "connected")

    send_context_button.on_click = do_send_context

    def toggle_mute(_event) -> None:
        audio.muted = not audio.muted
        if audio.muted:
            audio.clear_mic_queue()
        mute_button.text = "Mikro wieder einschalten" if audio.muted else "Mikro stummschalten"
        mute_button.icon = ft.Icons.MIC_OFF if audio.muted else ft.Icons.MIC
        page.update()

    mute_button.on_click = toggle_mute

    async def shutdown() -> None:
        await session.stop()
        audio.stop()

    page.on_disconnect = lambda _e: asyncio.create_task(shutdown())

    # --- Start ---
    try:
        audio.start()
    except Exception as exc:
        set_status(
            f"Audio-Gerät nicht startbar: {exc}. "
            "Prüfe, ob Mikro/Lautsprecher als Standardgerät eingerichtet sind.",
            "error",
        )
        return

    feeder_task = asyncio.create_task(audio.speaker_feeder())
    try:
        await session.run()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        set_status(f"Verbindungsfehler: {exc}", "error")
    finally:
        feeder_task.cancel()
        audio.stop()


ft.run(main)
