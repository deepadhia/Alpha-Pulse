"""Presentation of exit decisions and paper settlements; no trading decisions."""
from html import escape


def format_exit_card(position, reason=None):
    p = position
    forward = p.get("entry_source") == "RESTING_AUTOFILL"
    paper = p.get("status") in ("PAPER_ONLY", "PAPER_CLOSED")
    book = "P RESTING PAPER FORWARD" if forward else "PAPER" if paper else "SCANNER (broker execution unverified)"
    if forward and p.get("exit_settled"):
        model = p.get("exit_fill_model")
        basis = "opening quote" if model == "quote_bid_open" else "modelled" if model else "pricing model unavailable"
        status = f"Settled paper exit ({basis}; broker execution unverified)"
        pnl_label = "Paper P&L"
    elif forward:
        status = "Provisional; awaiting next-open settlement"
        pnl_label = "Provisional P&L"
    else:
        status = "Exit decision recorded; broker sale unverified"
        pnl_label = "P&L at recorded exit price"
    days = int(float(p.get("days_held") or 0))
    lines = [f"<b>{escape(str(p.get('symbol') or 'Unknown'))}</b>", f"Book: {book}",
             f"Held: {days} day{'s' if days != 1 else ''}", f"Exit status: {status}"]
    for key, label in (("entry_price", "Entry price"), ("exit_price", "Recorded exit price")):
        if p.get(key) is not None:
            lines.append(f"{label}: ₹{float(p[key]):,.2f}")
    if p.get("pnl_pct") is not None:
        lines.append(f"{pnl_label}: {float(p['pnl_pct']):+.2f}%")
    lines.append(f"Reason: {escape(str(reason if reason is not None else p.get('exit_reason') or 'Unavailable'))}")
    if p.get("outcome_type"):
        lines.append(f"Outcome: {escape(str(p['outcome_type']).replace('_', ' ').title())}")
    if p.get("max_runup_pct") is not None:
        lines.append(f"Peak run-up: {float(p['max_runup_pct']):+.1f}%")
    return "\n".join(lines)
