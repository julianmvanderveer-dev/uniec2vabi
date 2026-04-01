"""
app.py — Uniec2Vabi (Brynt.nl)

Betaalstroom:
  GET  /                Upload pagina
  POST /upload          Bestand inlezen → preview + telling → redirect /checkout/<id>
  GET  /checkout/<id>   Preview + prijsopgave (of gratis knop bij 1 woning)
  POST /convert-free/<id>  Gratis conversie (≤ FREE_UP_TO woningen)
  POST /pay/<id>        Mollie betaling aanmaken → redirect iDEAL
  GET  /return          Terugkeer na betaling → download of wachtpagina
  GET  /wait/<id>       Wachtpagina met auto-refresh
  POST /webhook         Mollie server-to-server bevestiging
"""

import io
import os
import time
import uuid
import threading

from flask import (
    Flask, request, redirect, url_for,
    render_template, send_file, flash,
)
from mollie.api.client import Client as MollieClient

from uniec3_to_vabi import convert as uniec3_convert, Uniec3Data, _prop
import config

# ── App ───────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'brynt-uniec2vabi-change-in-prod')

# ── Mollie ────────────────────────────────────────────────────────────────────

mollie = MollieClient()
mollie.set_api_key(os.environ.get('MOLLIE_API_KEY', 'test_VERVANG_MET_JOUW_SLEUTEL'))

# ── Tijdelijke opslag ─────────────────────────────────────────────────────────

_store: dict = {}
_lock = threading.Lock()


def _price(count: int) -> float:
    return round(count * config.PRICE_PER_DWELLING, 2)


def _is_free(count: int) -> bool:
    return count <= config.FREE_UP_TO


def _cleanup() -> None:
    cutoff = time.time() - 7200
    with _lock:
        stale = [k for k, v in _store.items() if v['created_at'] < cutoff]
        for k in stale:
            del _store[k]


def _build_preview(data: Uniec3Data) -> dict:
    """Haal overzichtsdata op voor de preview op de checkout pagina."""
    woningen = []
    for unit in data.entities_by_type.get('UNIT', []):
        naam  = _prop(unit, 'UNIT_OMSCHR') or '(naamloos)'
        uid   = unit['NTAEntityDataId']

        # Adres ophalen via AFMELDOBJECT → AFMELDLOCATIE
        adres = ''
        afm_obj = next((c for c in data.children_of.get(uid, [])
                        if c.get('NTAEntityId') == 'AFMELDOBJECT'), None)
        if afm_obj:
            afm_loc = next((c for c in data.children_of.get(
                            afm_obj['NTAEntityDataId'], [])
                            if c.get('NTAEntityId') == 'AFMELDLOCATIE'), None)
            if afm_loc:
                straat    = _prop(afm_loc, 'AFMELDLOCATIE_STRAAT')
                huisnr    = _prop(afm_loc, 'AFMELDLOCATIE_HUISNR')
                postcode  = _prop(afm_loc, 'AFMELDLOCATIE_PC')
                woonplaats = _prop(afm_loc, 'AFMELDLOCATIE_WOONPL')
                parts = [p for p in [straat, huisnr, postcode, woonplaats] if p]
                adres = ' '.join(parts)

        woningen.append({'naam': naam, 'adres': adres})

    return {
        'woningen':          woningen,
        'n_vlakken':         len(data.entities_by_type.get('BEGR', [])),
        'n_ramen_deuren':    len(data.entities_by_type.get('CONSTRT', [])),
        'n_koudebruggen':    len(data.entities_by_type.get('CONSTRL', [])),
        'heeft_ventilatie':  bool(data.entities_by_type.get('VENTSYS', [])),
        'heeft_verwarming':  bool(data.entities_by_type.get('VERW-OPWEK', [])
                                  or data.entities_by_type.get('VERW-INST', [])),
        'heeft_tapwater':    bool(data.entities_by_type.get('TAPW-OPWEK', [])
                                  or data.entities_by_type.get('TAPW-INST', [])),
    }


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
            'filename':   f.filename,
            'count':      count,
            'preview':    preview,
            'created_at': time.time(),
            'payment_id': None,
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
        is_free=_is_free(count),
    )


@app.route('/convert-free/<file_id>', methods=['POST'])
def convert_free(file_id):
    """Gratis conversie voor bestanden met ≤ FREE_UP_TO woningen."""
    with _lock:
        entry = _store.get(file_id)
    if not entry:
        flash('Sessie verlopen. Upload het bestand opnieuw.', 'error')
        return redirect(url_for('index'))

    if not _is_free(entry['count']):
        flash('Dit bestand valt niet binnen de gratis limiet.', 'error')
        return redirect(url_for('checkout', file_id=file_id))

    return _serve_conversion(file_id, entry)


@app.route('/pay/<file_id>', methods=['POST'])
def pay(file_id):
    with _lock:
        entry = _store.get(file_id)
    if not entry:
        flash('Sessie verlopen. Upload het bestand opnieuw.', 'error')
        return redirect(url_for('index'))

    count  = entry['count']
    amount = _price(count)
    label  = 'woning' if count == 1 else 'woningen'

    try:
        payment = mollie.payments.create({
            'amount': {
                'currency': 'EUR',
                'value':    f'{amount:.2f}',
            },
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
        return _serve_conversion(file_id, entry)

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
            return _serve_conversion(file_id, entry)
    except Exception:
        pass

    return render_template('wait.html', file_id=file_id)


@app.route('/webhook', methods=['POST'])
def webhook():
    payment_id = request.form.get('id', '')
    if payment_id:
        try:
            mollie.payments.get(payment_id)
        except Exception:
            pass
    return '', 200


# ── Hulpfunctie ───────────────────────────────────────────────────────────────

def _serve_conversion(file_id: str, entry: dict):
    try:
        project_naam = os.path.splitext(entry['filename'])[0]
        epa_bytes    = uniec3_convert(entry['bytes'], project_naam=project_naam)
    except Exception as e:
        flash(f'Fout bij conversie: {e}', 'error')
        return redirect(url_for('index'))

    with _lock:
        _store.pop(file_id, None)

    stem = os.path.splitext(entry['filename'])[0][:60]
    buf  = io.BytesIO(epa_bytes)
    buf.seek(0)
    return send_file(
        buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'{stem}.epa',
    )


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    app.run(debug=False, host='0.0.0.0', port=port)
