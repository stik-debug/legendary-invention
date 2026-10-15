"""Read-only matching of a bank/M-Pesa CSV export against recorded ChamaPay ledger entries."""
import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation

MAX_BYTES = 256 * 1024
MAX_ROWS = 500


def parse_statement_csv(raw):
    if isinstance(raw, bytes):
        if len(raw) > MAX_BYTES:
            raise ValueError('Statement CSV must be 256 KB or smaller.')
        try:
            raw = raw.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise ValueError('Save the statement CSV as UTF-8 and try again.') from exc
    if not raw or len(raw.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Upload or paste a statement CSV (maximum 256 KB).')
    reader = csv.DictReader(io.StringIO(raw))
    headers = {str(h or '').strip().lower() for h in (reader.fieldnames or [])}
    if not {'date', 'amount_kes'}.issubset(headers):
        raise ValueError('Statement CSV needs headers date,amount_kes. Optional headers: reference,description.')
    rows = []
    for line, row in enumerate(reader, start=2):
        if len(rows) >= MAX_ROWS:
            raise ValueError('Statement matching is limited to 500 rows per upload.')
        clean = {str(k or '').strip().lower(): str(v or '').strip() for k, v in row.items()}
        if not clean.get('date') and not clean.get('amount_kes'):
            continue
        try:
            on = date.fromisoformat(clean.get('date', ''))
            amount = Decimal(clean.get('amount_kes', '').replace(',', '').replace('KES', '').strip())
            if not amount.is_finite() or amount <= 0 or amount > Decimal('999999999'):
                raise InvalidOperation
            cents_decimal = amount * 100
            if cents_decimal != cents_decimal.to_integral_value():
                raise InvalidOperation
        except (ValueError, InvalidOperation):
            raise ValueError(f'Row {line}: enter a valid YYYY-MM-DD date and positive KES amount (maximum 2 decimals).')
        rows.append({'line': line, 'date': on.isoformat(), 'amount_cents': int(cents_decimal),
                     'reference': clean.get('reference', '')[:120], 'description': clean.get('description', '')[:240]})
    if not rows:
        raise ValueError('No statement transaction rows found.')
    return rows


def match_statement_rows(statement_rows, ledger_rows):
    """Never posts or changes money. Exact reference matches are stronger than date/amount-only candidates."""
    used = set()
    output = []
    for row in statement_rows:
        candidates = [x for x in ledger_rows if int(x.get('id') or 0) not in used
                      and int(x.get('amount_cents') or 0) == row['amount_cents']
                      and str(x.get('occurred_on') or '')[:10] == row['date']
                      and str(x.get('direction') or '') in ('IN', 'OUT')]
        ref = row['reference'].strip().casefold()
        if ref:
            exact = [x for x in candidates if str(x.get('reference') or '').strip().casefold() == ref]
            if len(exact) == 1:
                match = exact[0]; status = 'MATCHED'
            elif len(exact) > 1:
                match = None; status = 'AMBIGUOUS'
            elif candidates:
                match = None; status = 'REFERENCE_MISMATCH'
            else:
                match = None; status = 'UNMATCHED'
        elif len(candidates) == 1:
            match = candidates[0]; status = 'POSSIBLE_MATCH'
        elif len(candidates) > 1:
            match = None; status = 'AMBIGUOUS'
        else:
            match = None; status = 'UNMATCHED'
        if match:
            used.add(int(match['id']))
        output.append({**row, 'status': status, 'ledger_id': match.get('id') if match else None,
                       'ledger_kind': match.get('kind') if match else None,
                       'ledger_description': match.get('description') if match else None})
    return output
