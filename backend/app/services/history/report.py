"""Session reports and TXT / JSON / PDF exports."""
from __future__ import annotations

import io
import json
import re
from datetime import timezone
from xml.sax.saxutils import escape

from app.config.settings import Settings
from app.models.entities import ConversationSession, SessionMetrics
from app.services.history.store import _iso, message_to_dict


def format_duration(seconds: float | None) -> str:
    total = int(seconds or 0)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _electricity(active_seconds: float, settings: Settings) -> dict | None:
    """Only produced when the user has set a tariff. It is an estimate, not a measurement."""
    if settings.electricity_price_per_kwh is None:
        return None
    kwh = settings.power_watts * active_seconds / 3600 / 1000
    return {
        "kwh": round(kwh, 5),
        "cost": round(kwh * settings.electricity_price_per_kwh, 5),
        "currency": settings.electricity_currency,
        "assumed_watts": settings.power_watts,
    }


def build_report(session: ConversationSession, metrics: SessionMetrics | None, settings: Settings) -> dict:
    messages = [message_to_dict(m) for m in session.messages]
    meta = (metrics.model_metadata if metrics else None) or {}
    return {
        "session": {
            "id": session.id,
            "title": session.title,
            "status": session.status,
            "start_time": _iso(session.start_time),
            "end_time": _iso(session.end_time),
            "duration_seconds": session.duration,
            "active_seconds": session.active_duration,
            "paused_seconds": session.paused_duration,
            "user_messages": sum(m["speaker"] == "user" for m in messages),
            "ai_responses": sum(m["speaker"] == "assistant" for m in messages),
            "interruptions": metrics.interruption_count if metrics else 0,
        },
        "performance": {
            "average_response_latency_ms": metrics.average_latency if metrics else None,
            "fastest_response_ms": metrics.minimum_latency if metrics else None,
            "slowest_response_ms": metrics.maximum_latency if metrics else None,
            "average_transcription_latency_ms": metrics.average_transcription_latency if metrics else None,
            "average_llm_first_token_ms": metrics.average_llm_first_token if metrics else None,
            "average_interruption_latency_ms": metrics.average_interruption_latency if metrics else None,
            "user_speaking_seconds": metrics.user_speaking_duration if metrics else 0,
            "ai_speaking_seconds": metrics.ai_speaking_duration if metrics else 0,
            "turns": metrics.turns if metrics else [],
        },
        "cost": {
            "external_api_cost_usd": session.estimated_cost,
            "llm_model": meta.get("llm"),
            "stt_model": meta.get("stt"),
            "tts_model": meta.get("tts"),
            "electricity_estimate": _electricity(session.active_duration, settings),
            "note": "No paid AI APIs are used. Hardware, electricity and internet costs still apply.",
        },
        "transcript": messages,
    }


def safe_filename(title: str, extension: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9 _-]+", "", title).strip().replace(" ", "_")[:60]
    return f"{stem or 'conversation'}.{extension}"


def _ms(value: float | None) -> str:
    return "not measured" if value is None else f"{value:.0f} ms"


def _clock(iso: str | None) -> str:
    return iso.replace("T", " ")[:19] + " UTC" if iso else "-"


def _lines(report: dict) -> list[tuple[str, str]]:
    s, p, c = report["session"], report["performance"], report["cost"]
    rows = [
        ("Start", _clock(s["start_time"])),
        ("End", _clock(s["end_time"])),
        ("Total duration", format_duration(s["duration_seconds"])),
        ("Active duration", format_duration(s["active_seconds"])),
        ("Paused duration", format_duration(s["paused_seconds"])),
        ("User messages", str(s["user_messages"])),
        ("AI responses", str(s["ai_responses"])),
        ("Interruptions", str(s["interruptions"])),
        ("Average first-audio latency", _ms(p["average_response_latency_ms"])),
        ("Fastest response", _ms(p["fastest_response_ms"])),
        ("Slowest response", _ms(p["slowest_response_ms"])),
        ("Average transcription latency", _ms(p["average_transcription_latency_ms"])),
        ("External API cost", f"${c['external_api_cost_usd']:.2f}"),
        ("AI model", c["llm_model"] or "-"),
        ("Speech recognition model", c["stt_model"] or "-"),
        ("Text-to-speech model", c["tts_model"] or "-"),
    ]
    if estimate := c["electricity_estimate"]:
        rows.append(
            ("Estimated electricity", f"{estimate['kwh']} kWh, about {estimate['cost']} {estimate['currency']}")
        )
    return rows


def export_txt(report: dict) -> str:
    out = [f"Jarvis conversation: {report['session']['title']}", "=" * 60]
    out += [f"{label}: {value}" for label, value in _lines(report)]
    out += ["", report["cost"]["note"], "", "Transcript", "-" * 60]
    for m in report["transcript"]:
        tag = " [interrupted]" if m["interrupted"] else ""
        out.append(f"[{_clock(m['timestamp'])}] {'You' if m['speaker'] == 'user' else 'Jarvis'}{tag}: {m['content']}")
    return "\n".join(out) + "\n"


def export_json(report: dict) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False)


def export_pdf(report: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9.5, leading=13)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=report["session"]["title"], author="Jarvis",
    )
    # Every value goes through escape(): transcripts are user content and Paragraph parses markup.
    story = [Paragraph(f"Jarvis report: {escape(report['session']['title'])}", styles["Title"])]
    table = Table(
        [[Paragraph(escape(a), body), Paragraph(escape(b), body)] for a, b in _lines(report)],
        colWidths=[62 * mm, 110 * mm],
    )
    table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    story += [table, Spacer(1, 4 * mm), Paragraph(escape(report["cost"]["note"]), body), Spacer(1, 4 * mm)]
    story.append(Paragraph("Transcript", styles["Heading2"]))
    for m in report["transcript"]:
        who = "You" if m["speaker"] == "user" else "Jarvis"
        tag = " (interrupted)" if m["interrupted"] else ""
        story.append(
            Paragraph(f"<b>{who}{tag}</b> <font color='#777777'>{escape(_clock(m['timestamp']))}</font>", body)
        )
        story.append(Paragraph(escape(m["content"]), body))
        story.append(Spacer(1, 2 * mm))
    doc.build(story)
    return buffer.getvalue()
