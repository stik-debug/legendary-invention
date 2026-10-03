"""Reads M-Pesa confirmation messages (and simple 'CODE amount' lines) into structured data.

This only READS text. It cannot tell whether a message is genuine: anyone can type a message that looks right.
Genuineness is checked elsewhere, by matching the code and amount against what the chama itself received (see chama_pay.py).

Safaricom words these messages in several ways (send money, paybill, till, received, business), so the parser is tolerant:
it looks for the pieces it needs (10-character code, amount, date/time, other party) and returns None for any it cannot find."""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

CODE = r'[A-Z0-9]{10}'
_SMS_HEAD = re.compile(r'\b(' + CODE + r')\s+(?:CONFIRMED|COMPLETED)\b', re.I)
_LINE = re.compile(r'^\s*(' + CODE + r')\s*[\s,;:|-]+\s*(?:KES|KSH|SH)?\.?\s*([\d,]+(?:\.\d{1,2})?)\s*$', re.I | re.M)
_AMOUNT = re.compile(r'(?:KES|KSH|SH)\.?\s*([\d,]+(?:\.\d{1,2})?)', re.I)
_WHEN = re.compile(r'\bon\s+(\d{1,2})/(\d{1,2})/(\d{2,4})\s+at\s+(\d{1,2}):(\d{2})\s*([AP]M)?', re.I)
_PARTY = re.compile(r'(?:sent to|paid to|received from|from)\s+(.+?)(?=\s+for account|\s+on\s+\d|\s+\d{9,}|\.\s|\.$|$)', re.I)
MAX_RECORDS = 200


def looks_like_code(s):
    """M-Pesa codes always mix letters and digits, which keeps ordinary words and plain numbers from being read as codes."""
    s = (s or '').upper()
    return bool(re.fullmatch(CODE, s)) and any(c.isdigit() for c in s) and any(c.isalpha() for c in s)


def _cents(raw):
    try:
        d = Decimal(raw.replace(',', ''))
    except (InvalidOperation, ValueError, AttributeError):
        return None
    c = int(d * 100)
    return c if c > 0 and d == d.quantize(Decimal('0.01')) else None


def _when(text):
    """(date 'YYYY-MM-DD', time 'HH:MM') in the message's own (East Africa) clock, or (None, None)."""
    m = _WHEN.search(text)
    if not m:
        return None, None
    d, mo, y, h, mi, ap = m.groups()
    y = int(y) + (2000 if len(y) == 2 else 0)
    h = int(h)
    if ap:
        h = h % 12 + (12 if ap.upper() == 'PM' else 0)
    try:
        return datetime(y, int(mo), int(d), h, int(mi)).strftime('%Y-%m-%d'), f'{h:02d}:{mi}'
    except ValueError:
        return None, None


def parse_one(text):
    """One M-Pesa message -> {'code','amount_cents','date','time','party'} or None when no code is found.
    The amount is the FIRST amount after the code; the balance and the transaction cost always come later."""
    text = (text or '').strip()
    m = _SMS_HEAD.search(text)
    if not m:
        m = re.match(r'\s*(' + CODE + r')\b', text, re.I)
        if not m or not looks_like_code(m.group(1)):
            return None
    code = m.group(1).upper()
    rest = text[m.end():]
    a = _AMOUNT.search(rest)
    d, t = _when(rest)
    p = _PARTY.search(rest)
    return {'code': code, 'amount_cents': _cents(a.group(1)) if a else None, 'date': d, 'time': t,
            'party': (p.group(1).strip()[:60] if p else None)}


def parse_many(text):
    """Pasted text with many messages and/or 'CODE amount' lines -> list of parsed dicts (each code once, in order)."""
    text = (text or '')[:100_000]
    found, seen = [], set()
    heads = list(_SMS_HEAD.finditer(text))
    for i, h in enumerate(heads):
        block = text[h.start(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        r = parse_one(block)
        if r and r['code'] not in seen:
            seen.add(r['code']); found.append(r)
    for m in _LINE.finditer(text):
        code = m.group(1).upper()
        if code not in seen and looks_like_code(code):
            seen.add(code); found.append({'code': code, 'amount_cents': _cents(m.group(2)), 'date': None, 'time': None, 'party': None})
    return found[:MAX_RECORDS]


def mask(party):
    """Phone numbers in a message are shown shortened (0712***78) so the app does not keep other people's full numbers."""
    return re.sub(r'\d{9,13}', lambda x: x.group()[:4] + '***' + x.group()[-2:], party) if party else party


def summary(p):
    """One short line for officials: who the money went to and when, with no balance and no full phone numbers."""
    bits = [mask(p.get('party')) or None, ' '.join(x for x in (p.get('date'), p.get('time')) if x) or None]
    return ' · '.join(b for b in bits if b)[:120] or None
