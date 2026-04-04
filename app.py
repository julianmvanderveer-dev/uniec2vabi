"""
app.py — Uniec2Vabi (Brynt.nl)

Routes:
  GET  /                    Upload pagina
  POST /upload              Bestand verwerken → redirect /checkout/<id>
  GET  /checkout/<id>       Preview + klantgegevens + betaalknop
  POST /convert-free/<id>   Gratis conversie (≤ FREE_UP_TO woningen)
  POST /pay/<id>            Mollie betaling aanmaken → iDEAL
  GET  /return              Terugkeer na betaling
  GET  /wait/<id>           Wachtpagina
  POST /webhook             Mollie webhook
  GET  /success/<id>        Successpagina (.epa + factuur download)
  GET  /download-epa/<id>   .epa bestand downloaden
  GET  /download-invoice/<id>  Factuur PDF downloaden
  GET  /admin?key=...       Factuuroverzicht
"""

import io
import os
import time
import uuid
import threading
from datetime import datetime

from flask import (
    Flask, request, redirect, url_for,
    render_template, send_file, flash,
)
from fpdf import FPDF
from mollie.api.client import Client as MollieClient

from uniec3_to_vabi import convert as uniec3_convert, Uniec3Data, _prop
import config

# ── App ───────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'brynt-uniec2vabi-change-in-prod')

# ── Mollie ────────────────────────────────────────────────────────────────────

mollie = MollieClient()
mollie.set_api_key(os.environ.get('MOLLIE_API_KEY', 'test_VERVANG_MET_JOUW_SLEUTEL'))

# ── Opslag ────────────────────────────────────────────────────────────────────
# {file_id: {bytes, epa_bytes, filename, count, preview,
#            customer, invoice_nr, created_at, payment_id}}

_store: dict   = {}
_invoices: list = []   # voor /admin overzicht (in-memory, niet persistent)
_lock = threading.Lock()


# ── Hulpfuncties ──────────────────────────────────────────────────────────────

def _price(count: int) -> float:
    return round(count * config.PRICE_PER_DWELLING, 2)


def _vat(excl: float) -> float:
    return round(excl * config.VAT_RATE, 2)


def _is_free(count: int) -> bool:
    return count <= config.FREE_UP_TO


def _invoice_nr() -> str:
    return 'BRYNT-' + datetime.now().strftime('%Y%m%d-%H%M%S')


def _cleanup() -> None:
    cutoff = time.time() - 7200
    with _lock:
        stale = [k for k, v in _store.items() if v['created_at'] < cutoff]
        for k in stale:
            del _store[k]


def _build_preview(data: Uniec3Data) -> dict:
    woningen = []
    for unit in data.entities_by_type.get('UNIT', []):
        naam = _prop(unit, 'UNIT_OMSCHR') or '(naamloos)'
        uid  = unit['NTAEntityDataId']
        adres = ''
        afm_obj = next((c for c in data.children_of.get(uid, [])
                        if c.get('NTAEntityId') == 'AFMELDOBJECT'), None)
        if afm_obj:
            afm_loc = next((c for c in data.children_of.get(
                            afm_obj['NTAEntityDataId'], [])
                            if c.get('NTAEntityId') == 'AFMELDLOCATIE'), None)
            if afm_loc:
                parts = [p for p in [
                    _prop(afm_loc, 'AFMELDLOCATIE_STRAAT'),
                    _prop(afm_loc, 'AFMELDLOCATIE_HUISNR'),
                    _prop(afm_loc, 'AFMELDLOCATIE_PC'),
                    _prop(afm_loc, 'AFMELDLOCATIE_WOONPL'),
                ] if p]
                adres = ' '.join(parts)
        woningen.append({'naam': naam, 'adres': adres})

    return {
        'woningen':         woningen,
        'n_vlakken':        len(data.entities_by_type.get('BEGR', [])),
        'n_ramen_deuren':   len(data.entities_by_type.get('CONSTRT', [])),
        'n_koudebruggen':   len(data.entities_by_type.get('CONSTRL', [])),
        'heeft_ventilatie': bool(data.entities_by_type.get('VENTSYS', [])),
        'heeft_verwarming': bool(data.entities_by_type.get('VERW-OPWEK', [])
                                 or data.entities_by_type.get('VERW-INST', [])),
        'heeft_tapwater':   bool(data.entities_by_type.get('TAPW-OPWEK', [])
                                 or data.entities_by_type.get('TAPW-INST', [])),
    }


def _generate_invoice_pdf(entry: dict) -> bytes:
    """Genereer factuur als PDF-bytes."""
    c      = entry['customer']
    count  = entry['count']
    excl   = _price(count)
    vat    = _vat(excl)
    incl   = round(excl + vat, 2)
    nr     = entry.get('invoice_nr', _invoice_nr())
    datum  = datetime.now().strftime('%d-%m-%Y')

    pdf = FPDF()
    pdf.add_page()
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(auto=True, margin=20)

    # ── Header ──
    pdf.set_font('Helvetica', 'B', 18)
    pdf.set_text_color(19, 78, 74)   # brynt-900
    pdf.cell(0, 10, config.BEDRIJF_HANDELSNAAM, ln=True)

    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 5, config.BEDRIJF_NAAM, ln=True)
    if config.BEDRIJF_ADRES:
        pdf.cell(0, 5,
                 f'{config.BEDRIJF_ADRES}, {config.BEDRIJF_POSTCODE} {config.BEDRIJF_PLAATS}',
                 ln=True)
    if config.BEDRIJF_KVK:
        pdf.cell(0, 5, f'KvK: {config.BEDRIJF_KVK}', ln=True)
    if config.BEDRIJF_BTW:
        pdf.cell(0, 5, f'BTW: {config.BEDRIJF_BTW}', ln=True)

    pdf.ln(8)
    pdf.set_draw_color(200, 200, 200)
    pdf.set_line_width(0.3)
    pdf.line(20, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(6)

    # ── Factuurtitel ──
    pdf.set_font('Helvetica', 'B', 14)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 8, 'FACTUUR', ln=True)
    pdf.ln(2)

    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(60, 6, f'Factuurnummer:', ln=False)
    pdf.set_font('Helvetica', 'B', 9)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 6, nr, ln=True)

    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(60, 6, 'Factuurdatum:', ln=False)
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 6, datum, ln=True)

    pdf.ln(6)

    # ── Klantgegevens ──
    pdf.set_font('Helvetica', 'B', 9)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 6, 'FACTUUR AAN', ln=True)
    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 5, c.get('naam', ''), ln=True)
    if c.get('bedrijf'):
        pdf.cell(0, 5, c['bedrijf'], ln=True)
    pdf.cell(0, 5, c.get('email', ''), ln=True)
    if c.get('btw_nr'):
        pdf.cell(0, 5, f'BTW-nr: {c["btw_nr"]}', ln=True)

    pdf.ln(8)

    # ── Regels ──
    pdf.set_fill_color(240, 253, 250)
    pdf.set_font('Helvetica', 'B', 9)
    pdf.set_text_color(19, 78, 74)
    pdf.cell(100, 7, 'Omschrijving', border='B', fill=True)
    pdf.cell(20, 7, 'Aantal', border='B', fill=True, align='C')
    pdf.cell(30, 7, 'Prijs/st', border='B', fill=True, align='R')
    pdf.cell(30, 7, 'Totaal', border='B', fill=True, align='R', ln=True)

    pdf.set_font('Helvetica', '', 9)
    pdf.set_text_color(30, 30, 30)
    label = 'woning' if count == 1 else 'woningen'
    pdf.cell(100, 7, f'Uniec3 \u2192 VABI EPA conversie ({label})')
    pdf.cell(20, 7, str(count), align='C')
    pdf.cell(30, 7, f'\u20ac {config.PRICE_PER_DWELLING:.2f}', align='R')
    pdf.cell(30, 7, f'\u20ac {excl:.2f}', align='R', ln=True)

    pdf.ln(4)

    # ── Totalen ──
    def _totaal_rij(label, bedrag, bold=False):
        pdf.set_font('Helvetica', 'B' if bold else '', 9)
        pdf.cell(150, 6, label, align='R')
        pdf.cell(30, 6, f'\u20ac {bedrag:.2f}', align='R', ln=True)

    _totaal_rij('Subtotaal (excl. BTW)', excl)
    _totaal_rij(f'BTW {int(config.VAT_RATE * 100)}%', vat)
    pdf.set_draw_color(19, 78, 74)
    pdf.line(120, pdf.get_y(), 190, pdf.get_y())
    pdf.ln(1)
    _totaal_rij('Totaal (incl. BTW)', incl, bold=True)

    pdf.ln(8)

    # ── Betaalgegevens ──
    if config.BEDRIJF_IBAN:
        pdf.set_font('Helvetica', 'B', 9)
        pdf.set_text_color(80, 80, 80)
        pdf.cell(0, 5, 'Betaalgegevens', ln=True)
        pdf.set_font('Helvetica', '', 9)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(0, 5, f'IBAN: {config.BEDRIJF_IBAN}', ln=True)
        pdf.cell(0, 5, f'T.n.v.: {config.BEDRIJF_NAAM}', ln=True)
        pdf.cell(0, 5, f'Kenmerk: {nr}', ln=True)

    if entry.get('payment_id'):
        pdf.ln(3)
        pdf.set_font('Helvetica', '', 8)
        pdf.set_text_color(150, 150, 150)
        pdf.cell(0, 5, f'Betaald via iDEAL · Referentie: {entry["payment_id"]}', ln=True)

    pdf.ln(10)

    # ── Footer disclaimer ──
    pdf.set_font('Helvetica', 'I', 7)
    pdf.set_text_color(160, 160, 160)
    pdf.multi_cell(0, 4,
        'Aan de uitkomst van de conversie kunnen geen rechten worden ontleend. '
        'Brynt.nl is niet verantwoordelijk voor de juistheid van de omgezette informatie. '
        'Controleer het resultaat altijd zelf.')

    return bytes(pdf.output())


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload():
    _cleanup()
    f = request.files.get('file')
    if not f or f.filename == '':
        flash('Geen bestand geselecteerd.', 'error')
        return redirect(url_for('index'))
    if not f.filename.lower().endswith('.uniec3'):
        flash('Selecteer een geldig .uniec3 bestand.', 'error')
        return redirect(url_for('index'))

    raw = f.read()
    try:
        data    = Uniec3Data(raw)
        count   = len(data.entities_by_type.get('UNIT', []))
        preview = _build_preview(data)
    except Exception as e:
        flash(f'Fout bij inlezen bestand: {e}', 'error')
        return redirect(url_for('index'))

    if count == 0:
        flash('Geen woningen (UNIT) gevonden in dit bestand.', 'error')
        return redirect(url_for('index'))

    file_id = str(uuid.uuid4())
    with _lock:
        _store[file_id] = {
            'bytes':      raw,
            'epa_bytes':  None,
            'filename':   f.filename,
            'count':      count,
            'preview':    preview,
            'customer':   {},
            'invoice_nr': None,
            'payment_id': None,
            'created_at': time.time(),
        }
    return redirect(url_for('checkout', file_id=file_id))


@app.route('/checkout/<file_id>')
def checkout(file_id):
    with _lock:
        entry = _store.get(file_id)
    if not entry:
        flash('Sessie verlopen. Upload het bestand opnieuw.', 'error')
        return redirect(url_for('index'))
    count = entry['count']
    return render_template(
        'checkout.html',
        file_id=file_id,
        filename=entry['filename'],
        count=count,
        preview=entry['preview'],
        price_per=config.PRICE_PER_DWELLING,
        price_total=_price(count),
        vat=_vat(_price(count)),
        price_incl=round(_price(count) + _vat(_price(count)), 2),
        is_free=_is_free(count),
    )


@app.route('/convert-free/<file_id>', methods=['POST'])
def convert_free(file_id):
    with _lock:
        entry = _store.get(file_id)
    if not entry:
        flash('Sessie verlopen. Upload het bestand opnieuw.', 'error')
        return redirect(url_for('index'))
    if not _is_free(entry['count']):
        flash('Dit bestand valt niet binnen de gratis limiet.', 'error')
        return redirect(url_for('checkout', file_id=file_id))
    return _do_conversion_and_redirect(file_id, entry)


@app.route('/pay/<file_id>', methods=['POST'])
def pay(file_id):
    with _lock:
        entry = _store.get(file_id)
    if not entry:
        flash('Sessie verlopen. Upload het bestand opnieuw.', 'error')
        return redirect(url_for('index'))

    # Klantgegevens opslaan
    customer = {
        'naam':    request.form.get('naam', '').strip(),
        'bedrijf': request.form.get('bedrijf', '').strip(),
        'email':   request.form.get('email', '').strip(),
        'btw_nr':  request.form.get('btw_nr', '').strip(),
    }
    if not customer['naam'] or not customer['email']:
        flash('Vul je naam en e-mailadres in voor de factuur.', 'error')
        return redirect(url_for('checkout', file_id=file_id))

    invoice_nr = _invoice_nr()
    with _lock:
        if file_id in _store:
            _store[file_id]['customer']   = customer
            _store[file_id]['invoice_nr'] = invoice_nr

    count  = entry['count']
    amount = _price(count)
    label  = 'woning' if count == 1 else 'woningen'

    try:
        payment = mollie.payments.create({
            'amount':      {'currency': 'EUR', 'value': f'{amount:.2f}'},
            'description': f'Uniec2Vabi — {count} {label} ({entry["filename"]})',
            'redirectUrl': url_for('payment_return', file_id=file_id, _external=True),
            'webhookUrl':  url_for('webhook', _external=True),
            'metadata':    {'file_id': file_id},
            'method':      'ideal',
        })
    except Exception as e:
        flash(f'Fout bij aanmaken betaling: {e}', 'error')
        return redirect(url_for('checkout', file_id=file_id))

    with _lock:
        if file_id in _store:
            _store[file_id]['payment_id'] = payment.id

    return redirect(payment.checkout_url)


@app.route('/return')
def payment_return():
    file_id = request.args.get('file_id', '')
    with _lock:
        entry = _store.get(file_id)
    if not entry or not entry.get('payment_id'):
        flash('Onbekende sessie. Upload het bestand opnieuw.', 'error')
        return redirect(url_for('index'))

    try:
        payment = mollie.payments.get(entry['payment_id'])
        status  = payment.status
    except Exception as e:
        flash(f'Kon betaalstatus niet ophalen: {e}', 'error')
        return redirect(url_for('index'))

    if status == 'paid':
        return _do_conversion_and_redirect(file_id, entry)
    if status in ('pending', 'open', 'authorized'):
        return redirect(url_for('wait', file_id=file_id))

    flash('Betaling niet geslaagd. Probeer het opnieuw.', 'error')
    return redirect(url_for('checkout', file_id=file_id))


@app.route('/wait/<file_id>')
def wait(file_id):
    with _lock:
        entry = _store.get(file_id)
    if not entry:
        flash('Sessie verlopen.', 'error')
        return redirect(url_for('index'))
    try:
        payment = mollie.payments.get(entry['payment_id'])
        if payment.status == 'paid':
            return _do_conversion_and_redirect(file_id, entry)
    except Exception:
        pass
    return render_template('wait.html', file_id=file_id)


@app.route('/webhook', methods=['POST'])
def webhook():
    payment_id = request.form.get('id', '')
    if not payment_id:
        return '', 200
    try:
        payment = mollie.payments.get(payment_id)
        if payment.status != 'paid':
            return '', 200
        # Zoek file_id op via payment_id
        with _lock:
            file_id = next(
                (k for k, v in _store.items() if v.get('payment_id') == payment_id),
                None,
            )
        if file_id is None:
            return '', 200
        with _lock:
            entry = _store.get(file_id)
        if entry and not entry.get('epa_bytes'):
            _run_conversion(file_id, entry)
    except Exception:
        pass
    return '', 200


@app.route('/success/<file_id>')
def success(file_id):
    with _lock:
        entry = _store.get(file_id)
    if not entry or not entry.get('epa_bytes'):
        flash('Sessie verlopen. Neem contact op als je het bestand niet hebt ontvangen.', 'error')
        return redirect(url_for('index'))
    return render_template('success.html',
                           file_id=file_id,
                           filename=entry['filename'],
                           count=entry['count'],
                           is_free=_is_free(entry['count']))


@app.route('/download-epa/<file_id>')
def download_epa(file_id):
    with _lock:
        entry = _store.get(file_id)
    if not entry or not entry.get('epa_bytes'):
        flash('Download niet meer beschikbaar. Upload het bestand opnieuw.', 'error')
        return redirect(url_for('index'))
    stem = os.path.splitext(entry['filename'])[0][:60]
    buf  = io.BytesIO(entry['epa_bytes'])
    buf.seek(0)
    return send_file(buf, mimetype='application/zip',
                     as_attachment=True, download_name=f'{stem}.epa')


@app.route('/download-invoice/<file_id>')
def download_invoice(file_id):
    with _lock:
        entry = _store.get(file_id)
    if not entry or _is_free(entry.get('count', 0)):
        flash('Geen factuur beschikbaar.', 'error')
        return redirect(url_for('index'))
    pdf_bytes = _generate_invoice_pdf(entry)
    nr  = entry.get('invoice_nr', 'factuur')
    buf = io.BytesIO(pdf_bytes)
    buf.seek(0)
    return send_file(buf, mimetype='application/pdf',
                     as_attachment=True, download_name=f'{nr}.pdf')


@app.route('/admin')
def admin():
    key = request.args.get('key', '')
    if key != os.environ.get('ADMIN_KEY', config.ADMIN_KEY):
        return 'Toegang geweigerd.', 403
    with _lock:
        facturen = list(reversed(_invoices))
    return render_template('admin.html', facturen=facturen)


# ── Interne hulpfuncties ──────────────────────────────────────────────────────

def _run_conversion(file_id: str, entry: dict) -> bool:
    """Voer de conversie uit en sla het resultaat op in _store.
    Geeft True terug bij succes, False bij fout. Geen redirect."""
    try:
        project_naam = os.path.splitext(entry['filename'])[0]
        epa_bytes    = uniec3_convert(entry['bytes'], project_naam=project_naam)
    except Exception:
        return False

    with _lock:
        if file_id not in _store:
            return False
        _store[file_id]['epa_bytes'] = epa_bytes
        if not _is_free(entry['count']):
            _invoices.append({
                'nr':         _store[file_id].get('invoice_nr', ''),
                'datum':      datetime.now().strftime('%d-%m-%Y %H:%M'),
                'klant':      entry.get('customer', {}).get('naam', ''),
                'email':      entry.get('customer', {}).get('email', ''),
                'woningen':   entry['count'],
                'bedrag':     _price(entry['count']),
                'payment_id': entry.get('payment_id', ''),
                'bestand':    entry['filename'],
            })
    return True


def _do_conversion_and_redirect(file_id: str, entry: dict):
    """Converteer en redirect naar successpagina (voor browser-flow)."""
    if not entry.get('epa_bytes'):
        ok = _run_conversion(file_id, entry)
        if not ok:
            flash('Fout bij conversie. Probeer opnieuw.', 'error')
            return redirect(url_for('index'))
    return redirect(url_for('success', file_id=file_id))


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    app.run(debug=False, host='0.0.0.0', port=port)
