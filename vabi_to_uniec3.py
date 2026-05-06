"""
vabi_to_uniec3.py — Converteert een VABI EPA .epa bestand (utiliteitsgebouw)
naar een Uniec3 .uniec3 bestand.

.epa     = ZIP met project.xml (VABI EPA formaat 11.x)
.uniec3  = ZIP met buildings/{id}/entities.json + relations.json

Beperkingen:
- Installaties worden met forfaitaire standaardwaarden aangemaakt
- Gebouwhoogte (INFIL_BGH) wordt geschat op 4 m per bouwlaag
- Gebruiksfuncties worden afgeleid van VABI Hoofdfunctie
"""

import io
import json
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# ─── CONSTANTEN ───────────────────────────────────────────────────────────────

# VABI Locatie (int) → Uniec3 BEGR_VLAK
LOCATIE_TO_VLAK = {
    '0': 'VLAK_VLOER',
    '1': 'VLAK_DAK',
    '2': 'VLAK_GEVEL',
    '3': 'VLAK_GEVEL',
    '4': 'VLAK_GEVEL',
    '5': 'VLAK_GEVEL',
    '6': 'VLAK_VLOER_BOVBUI',
}

# VABI ConstructieType → LIBCONSTRD_TYPE (opaque)
CTYPE_TO_LIBCONSTRD = {
    '0': 'LIBVLAK_GEVEL',
    '4': 'LIBVLAK_DAK',
    '5': 'LIBVLAK_DAK',
    '6': 'LIBVLAK_DAK',
    '7': 'LIBVLAK_VLOER',
    '8': 'LIBVLAK_GEVEL',
}

# VABI Locatie → LIBCONSTRD_TYPE (fallback per locatie)
LOCATIE_TO_LIBCONSTRD = {
    '0': 'LIBVLAK_VLOER',
    '1': 'LIBVLAK_DAK',
    '2': 'LIBVLAK_GEVEL',
    '3': 'LIBVLAK_GEVEL',
    '4': 'LIBVLAK_GEVEL',
    '5': 'LIBVLAK_GEVEL',
    '6': 'LIBVLAK_VLOER',
}

# VABI Orientatie (int) → Uniec3 BEGR_GEVEL
ORI_MAP = {
    '0': 'Z', '1': 'ZW', '2': 'W', '3': 'NW',
    '4': 'N', '5': 'NO', '6': 'O', '7': 'ZO',
}

# VABI Hoofdfunctie (int) → Uniec3 GF code
HOOFDFUNCTIE_TO_GF = {
    '2':  'GF_BIJEEN',
    '3':  'GF_CEL',
    '4':  'GF_GEZONDH_BED',
    '5':  'GF_GEZONDH_ZBED',
    '6':  'GF_INDUSTRIE',
    '7':  'GF_KANTOOR',
    '8':  'GF_LOGIES',
    '9':  'GF_ONDERWIJS',
    '10': 'GF_SPORT',
    '11': 'GF_WINKEL',
    '12': 'GF_BIJEENOVER',
}

# VABI Verlichting Regeling → Uniec3 VERLZ_VERLREG
REGELING_TO_VERLREG = {
    '0': 'VERLZ_VERLREG_CA',
    '1': 'VERLZ_VERLREG_TW',
    '2': 'VERLZ_VERLREG_PA',
    '3': 'VERLZ_VERLREG_DL',
}


# ─── HULPFUNCTIES ─────────────────────────────────────────────────────────────

def _guid():
    return str(uuid.uuid4())


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fmt(val):
    """Float → Uniec3 string (komma decimaal)."""
    if val is None:
        return ''
    try:
        f = float(val)
        return f'{f:.2f}'.replace('.', ',')
    except (TypeError, ValueError):
        return str(val).replace('.', ',')


def _txt(el, *tags):
    """Lees tekst van een (geneste) XML child."""
    cur = el
    for tag in tags:
        if cur is None:
            return None
        cur = cur.find(tag)
    return cur.text if cur is not None else None


def _f(el, *tags):
    """Lees float van een XML element."""
    v = _txt(el, *tags)
    if v is None:
        return 0.0
    try:
        return float(v.replace(',', '.'))
    except ValueError:
        return 0.0


_BUILD_ID = None  # wordt ingesteld in convert()


def _entity(eid, etype, props, order=100.0):
    """Maak een Uniec3 entity dict."""
    prop_list = []
    ver = 10000
    for prop_id, value in props.items():
        entry = {
            'NTAPropertyId': prop_id,
            'NTAPropertyVersionId': ver,
            'NTAPropertyDataId': f'{eid}:{prop_id}',
            'Status': 2,
            'Timestamp': _now(),
        }
        if value != '':
            entry['Value'] = str(value)
        prop_list.append(entry)
        ver += 1
    return {
        'NTAEntityId': etype,
        'NTAEntityVersionId': 1000,
        'Order': order,
        'BuildingId': _BUILD_ID,
        'NTAEntityDataId': eid,
        'Status': 2,
        'NTAPropertyDatas': prop_list,
    }


def _rel(parent_id, parent_type, child_id, child_type):
    """Maak een Uniec3 relatie dict."""
    return {
        'ParentId': parent_id,
        'NTAEntityIdParent': parent_type,
        'ChildId': child_id,
        'NTAEntityIdChild': child_type,
        'BuildingId': _BUILD_ID,
        'NTAEntityRelationDataId': f'{parent_id}:{child_id}',
        'OnDelete': 1,
        'OnCopy': 1,
        'Timestamp': _now(),
    }


# ─── VABI EPA INLEZEN ─────────────────────────────────────────────────────────

def _read_vabi(epa_bytes):
    """Lees een VABI EPA bestand en retourneer een dict met de data."""
    with zipfile.ZipFile(io.BytesIO(epa_bytes)) as z:
        with z.open('project.xml') as f:
            tree = ET.parse(f)
    root = tree.getroot()

    # Constructies (globaal)
    constrs = {}
    for c in root.find('Constructies').findall('Constructie'):
        g = _txt(c, 'Guid')
        if g:
            constrs[g] = {
                'naam': _txt(c, 'Naam') or '',
                'type': _txt(c, 'ConstructieType') or '0',
                'rc':   _f(c, 'Rc'),
                'u':    _f(c, 'Uwaardeglasconstructie'),
                'g':    _f(c, 'Gwaarde'),
            }

    # Installaties (globaal, eerste)
    vent_systeem = '3'  # default: systeem C (mechanisch afvoer)
    koeling_aanwezig = False
    pv_list = []

    inst_global = root.find('Installaties')
    if inst_global is not None:
        for inst in inst_global.findall('Installatie'):
            vent = inst.find('Ventilatie')
            if vent is not None:
                vs = _txt(vent, 'Ventilatiesysteem')
                if vs:
                    vent_systeem = vs
            koel = inst.find('KoelingOpwekking')
            if koel is not None:
                if _txt(koel, 'KoelingAanwezig') == '1':
                    koeling_aanwezig = True
            zon_list = inst.find('ZonneEnergieList')
            if zon_list is not None:
                for zon in zon_list.findall('ZonneEnergie'):
                    if _txt(zon, 'TypeZonnepanelen') is not None:
                        pv_list.append(zon)

    # Objecten
    objecten = []
    for obj in root.find('Objecten').findall('Object'):
        oa = obj.find('ObjectAlgemeen')
        rzs = obj.find('Rekenzones')
        if rzs is None:
            continue

        rekenzones = []
        for rz in rzs.findall('Rekenzone'):
            alg = rz.find('Algemeen')
            naam = _txt(rz, 'Naam') or 'Rekenzone'
            bwjr = _txt(alg, 'Bouwjaar') if alg is not None else '2024'
            hfunc = _txt(alg, 'Hoofdfunctie') if alg is not None else '11'
            ag_raw = _f(alg, 'Gebruiksoppervlakte') if alg is not None else 0.0

            # Verlichting
            verlichtingen = []
            vl_list = rz.find('VerlichtingList')
            if vl_list is not None:
                for vl in vl_list.findall('Verlichting'):
                    verlichtingen.append({
                        'naam':    _txt(vl, 'Naam') or 'VZ',
                        'vermogen': _f(vl, 'RelevantTotaalVermogenPerM2'),
                        'pct_opp': _f(vl, 'PercentageOppervlakte'),
                        'regeling': _txt(vl, 'Regeling') or '0',
                        'daglicht': _txt(vl, 'DaglichtregelingAanwezig') or '0',
                        'kag30':    _txt(vl, 'KantoordeelMetSchakelzonesGroterDan30m2') or '0',
                    })

            # Geometrie
            hoofdvlakken = []
            geo = rz.find('Geometrie')
            if geo is not None:
                for hv in geo.findall('Hoofdvlak'):
                    if _txt(hv, 'BouwdeelIsInactief') == '1':
                        continue
                    locatie  = _txt(hv, 'Locatie') or '2'
                    orientatie = _txt(hv, 'Orientatie') or '0'
                    opp_bruto = _f(hv, 'Oppervlakte')
                    opp_netto = _f(hv, 'NettoOppervlakte')
                    constr_guid = _txt(hv, 'Constructie') or ''
                    hv_naam = _txt(hv, 'Naam') or 'Begrenzingsvlak'
                    hv_rc   = _f(hv, 'Rc')

                    deelvlakken = []
                    dvl = hv.find('DeelvlakList')
                    if dvl is not None:
                        for dv in dvl.findall('Deelvlak'):
                            dv_constr = _txt(dv, 'Constructie') or ''
                            dv_opp   = _f(dv, 'RelevanteOppervlakte')
                            dv_b     = _f(dv, 'Breedte')
                            dv_h     = _f(dv, 'HoogteOfLengte')
                            dv_u     = _f(dv, 'U')
                            dv_g     = _f(dv, 'G')
                            dv_naam  = _txt(dv, 'Naam') or ''

                            # U/g van deelvlak zelf (is ingevuld), anders van constructie
                            c_info = constrs.get(dv_constr, {})
                            c_type = c_info.get('type', '2')

                            if dv_u == 0.0:
                                dv_u = c_info.get('u', 0.0)
                            if dv_g == 0.0:
                                dv_g = c_info.get('g', 0.0)

                            # Belemmering
                            belem_l_a = _f(dv, 'BelemmeringLinksAfstand')
                            belem_l_b = _f(dv, 'BelemmeringLinksBreedte')
                            belem_r_a = _f(dv, 'BelemmeringRechtsAfstand')
                            belem_r_b = _f(dv, 'BelemmeringRechtsBreedte')
                            heeft_l = _txt(dv, 'BelemmeringLinks') == '1'
                            heeft_r = _txt(dv, 'BelemmeringRechts') == '1'

                            besch = 'n.v.t.'
                            if heeft_l and heeft_r:
                                besch = 'BELEMTYPE_ZIJ_BEIDE'
                            elif heeft_l:
                                besch = 'BELEMTYPE_ZIJ_LINKS'
                            elif heeft_r:
                                besch = 'BELEMTYPE_ZIJ_RECHTS'

                            deelvlakken.append({
                                'naam':    dv_naam,
                                'opp':     dv_opp,
                                'b':       dv_b,
                                'h':       dv_h,
                                'u':       dv_u,
                                'g':       dv_g,
                                'c_type':  c_type,
                                'c_naam':  c_info.get('naam', dv_naam),
                                'c_guid':  dv_constr,
                                'besch':   besch,
                                'l_a':     belem_l_a,
                                'l_b':     belem_l_b,
                                'r_a':     belem_r_a,
                                'r_b':     belem_r_b,
                            })

                    # Bereken opaque oppervlak als NettoOppervlakte niet gevuld
                    if opp_netto == 0.0 and opp_bruto > 0:
                        transp_sum = sum(d['opp'] for d in deelvlakken
                                         if d['c_type'] in ('2', '3'))
                        opp_netto = max(0.0, opp_bruto - transp_sum)

                    hoofdvlakken.append({
                        'naam':      hv_naam,
                        'locatie':   locatie,
                        'orientatie': orientatie,
                        'opp_bruto': opp_bruto,
                        'opp_netto': opp_netto,
                        'constr_guid': constr_guid,
                        'rc':        hv_rc if hv_rc > 0 else constrs.get(constr_guid, {}).get('rc', 0.0),
                        'deelvlakken': deelvlakken,
                    })

            # Bereken Ag uit vloeroppervlakken als niet ingevuld
            if ag_raw == 0.0:
                ag_raw = sum(hv['opp_bruto'] for hv in hoofdvlakken
                              if hv['locatie'] in ('0', '6'))

            rekenzones.append({
                'naam':          naam,
                'bwjr':          bwjr or '2024',
                'hoofdfunctie':  hfunc or '11',
                'ag':            ag_raw,
                'verlichtingen': verlichtingen,
                'hoofdvlakken':  hoofdvlakken,
                'vent_systeem':  vent_systeem,
                'koeling':       koeling_aanwezig,
            })

        obj_naam = _txt(obj.find('ObjectAlgemeen'), 'Naam') if obj.find('ObjectAlgemeen') else None
        if not obj_naam:
            obj_naam = 'Gebouw'

        objecten.append({
            'naam':       obj_naam,
            'rekenzones': rekenzones,
        })

    alg = root.find('Algemeen')
    bwjr = _txt(alg, 'Bouwjaar') if alg is not None else '2024'

    return {
        'naam':     _txt(root, 'FileName') or 'VABI import',
        'bwjr':     bwjr or '2024',
        'objecten': objecten,
        'constrs':  constrs,
    }


# ─── UNIEC3 AANMAKEN ──────────────────────────────────────────────────────────

def _add(entities, relations, eid, etype, props, order=100.0):
    e = _entity(eid, etype, props, order)
    entities.append(e)
    return eid


def _link(relations, parent_id, parent_type, child_id, child_type):
    relations.append(_rel(parent_id, parent_type, child_id, child_type))


def _build_entities(vabi):
    """Bouw de volledige lijst van Uniec3 entities en relations."""
    entities = []
    relations = []
    lib_constrd_map = {}   # constr_guid → libconstrd_eid
    lib_constrt_map = {}   # (u, g, c_type_code) → libconstrt_eid

    # ── Basis ──────────────────────────────────────────────────────────────────
    basis_id = _guid()
    _add(entities, relations, basis_id, 'BASIS', {'BASIS_DUMMY': ''})

    settings_id = _guid()
    _add(entities, relations, settings_id, 'SETTINGS', {
        'SETTINGS_MAATADVIES': 'False',
        'SETTINGS_ONLY_ACTU_VERKL': 'True',
        'SETTINGS_THBRUG': 'True',
        'SETTINGS_VARIANTEN': 'False',
    })

    climate_id = _guid()
    _add(entities, relations, climate_id, 'CLIMATE', {
        'CLIMATE_HEAT_ISLAND': '',
        'CLIMATE_KNMI_INV': '',
        'CLIMATE_KNMI_STATION': '',
        'CLIMATE_POSTCODE': '',
    })

    # ── GEB ────────────────────────────────────────────────────────────────────
    geb_id = _guid()
    gebouw_naam = vabi['naam']
    if objecten := vabi.get('objecten'):
        if rekenz := objecten[0].get('rekenzones'):
            bwjr_geb = rekenz[0].get('bwjr', vabi.get('bwjr', ''))
        else:
            bwjr_geb = vabi.get('bwjr', '')
    else:
        bwjr_geb = vabi.get('bwjr', '')

    _add(entities, relations, geb_id, 'GEB', {
        'GEB_BWJR':     bwjr_geb,
        'GEB_CALCNEEDED': 'false',
        'GEB_DATE':     _now(),
        'GEB_EIGEND':   'GEBEIGEND_ONBEKEND',
        'GEB_HASMELD':  'False',
        'GEB_OMSCHR':   gebouw_naam,
        'GEB_OPEN':     'true',
        'GEB_OPLVJR':   '',
        'GEB_OPN':      'OPN_DETAIL',
        'GEB_PL':       '394',
        'GEB_RENOVJR':  '',
        'GEB_SRTBW':    'NIEUWB',
        'GEB_TYPEGEB':  'TGEB_UTILIT',
    })

    geb_extra_id = _guid()
    _add(entities, relations, geb_extra_id, 'GEB-EXTRA', {
        'GEB-EXTRA_ADRS_GEB': '',
        'GEB-EXTRA_OMSCHR_GEB': '',
    })

    # ── INFIL ──────────────────────────────────────────────────────────────────
    infil_id = _guid()
    _add(entities, relations, infil_id, 'INFIL', {
        'INFIL_BGH':    '',
        'INFIL_INVOER': 'INFIL_GMW',
        'INFIL_OPEN':   'true',
        'INFIL_VERV_METHODE': 'INFIL_VERV_METHODE_FORF',
    })

    # ── VLEIDING ───────────────────────────────────────────────────────────────
    vleiding_id = _guid()
    _add(entities, relations, vleiding_id, 'VLEIDING', {
        'VLEIDING_INVOER': 'VLEIDINGL_ONBEKEND',
        'VLEIDING_TOI':    '2',
    })

    # ── LIBCONSTRFORM ──────────────────────────────────────────────────────────
    libconstrform_id = _guid()
    _add(entities, relations, libconstrform_id, 'LIBCONSTRFORM', {
        'LIBCONSTRFORM_KOZ':  'KOZKENM_GEEN',
        'LIBCONSTRFORM_OPEN': '',
    })

    # ── LIBCONSTRL ─────────────────────────────────────────────────────────────
    libconstrl_id = _guid()
    _add(entities, relations, libconstrl_id, 'LIBCONSTRL', {
        'LIBCONSTRL_BEPALING': '',
        'LIBCONSTRL_METH':     'LIN_VRIJE_INV',
        'LIBCONSTRL_OMSCHR':   '',
        'LIBCONSTRL_POS':      '',
        'LIBCONSTRL_PSI':      '',
    })

    # ── Per Object → UNIT ──────────────────────────────────────────────────────
    for obj_idx, obj in enumerate(vabi.get('objecten', [])):
        unit_id = _guid()
        _add(entities, relations, unit_id, 'UNIT', {
            'UNIT_OMSCHR':   obj.get('naam', 'Gebouw'),
            'UNIT_TYPEGEB':  'UNIL_GEB_ML',
            'UNIT_AANTA':    '',
            'UNIT_AANTU':    '',
        }, order=100.0 + obj_idx)

        infilunit_id = _guid()
        _add(entities, relations, infilunit_id, 'INFILUNIT', {
            'INFILUNIT_BGH':        '',
            'INFILUNIT_QV':         '',
            'INFILUNIT_QV_DEFAULT': '0.42',
            'INFILUNIT_QV_NON':     '0,42',
        })
        _link(relations, unit_id, 'UNIT', infilunit_id, 'INFILUNIT')

        prestatie_unit_id = _guid()
        _add(entities, relations, prestatie_unit_id, 'PRESTATIE', {
            'EP_BENG1': '', 'EP_BENG2': '', 'EP_BENG3': '',
            'EP_ENERGIELABEL': '',
        })
        _link(relations, unit_id, 'UNIT', prestatie_unit_id, 'PRESTATIE')

        # ── Per Rekenzone ──────────────────────────────────────────────────────
        for rz_idx, rz in enumerate(obj.get('rekenzones', [])):
            rz_id    = _guid()
            unit_rz_id = _guid()

            ag = rz.get('ag', 0.0)
            gf_code = HOOFDFUNCTIE_TO_GF.get(rz['hoofdfunctie'], 'GF_BIJEENOVER')

            _add(entities, relations, rz_id, 'RZ', {
                'RZ_BOUWLG':      '1',
                'RZ_BOUWW_VL':    'CONSTRM_FL_26',
                'RZ_BOUWW_W':     'CONSTRM_W_11',
                'RZ_CM':          'n.v.t.',
                'RZ_OMSCHR':      rz['naam'],
                'RZ_TYPEPLFND':   'TYPEPLFND_GEEN',
                'RZ_TYPEZ':       'RZ',
            }, order=100.0 + rz_idx)

            _add(entities, relations, unit_rz_id, 'UNIT-RZ', {
                'UNIT-RZBLAAG': '',
                'UNIT-RZCM':    'n.v.t.',
                'UNIT-RZID':    rz_id,
            }, order=100.0 + rz_idx)
            _link(relations, unit_id, 'UNIT', unit_rz_id, 'UNIT-RZ')

            # RZFORM
            rzform_id = _guid()
            _add(entities, relations, rzform_id, 'RZFORM', {
                'RZFORM_CALCUNIT': 'RZUNIT_GEB',
                'RZFORM_OPEN':     'true',
            })

            # UNIT-RZ-GF
            unit_rz_gf_id = _guid()
            _add(entities, relations, unit_rz_gf_id, 'UNIT-RZ-GF', {
                'UNIT-RZ-GFAG': _fmt(ag),
                'UNIT-RZ-GFID': gf_code,
            })
            _link(relations, unit_rz_id, 'UNIT-RZ', unit_rz_gf_id, 'UNIT-RZ-GF')

            # GRUIMTE (onder UNIT-RZ-GF)
            gruimte_id = _guid()
            _add(entities, relations, gruimte_id, 'GRUIMTE', {
                'GRUIMTE_AG':         '0,00',
                'GRUIMTE_AV_INVOER':  'GRUIMTE_AV_INVOER_RZ',
                'GRUIMTE_OMSCHR':     'Gemeenschappelijk',
                'GRUIMTE_UNITID':     '',
            })
            _link(relations, unit_rz_gf_id, 'UNIT-RZ-GF', gruimte_id, 'GRUIMTE')

            gruimte_begr_form_id = _guid()
            _add(entities, relations, gruimte_begr_form_id, 'BEGR-FORM', {
                'BEGR-FORM_OPEN': 'true',
            })
            _link(relations, gruimte_id, 'GRUIMTE', gruimte_begr_form_id, 'BEGR-FORM')

            # VENTCAP
            ventcap_id = _guid()
            _add(entities, relations, ventcap_id, 'VENTCAP', {
                'VENTCAP_MD': '', 'VENTCAP_MV': '', 'VENTCAP_NAOS': '',
                'VENTCAP_ND': '', 'VENTCAP_NV': '',
            })
            _link(relations, unit_rz_id, 'UNIT-RZ', ventcap_id, 'VENTCAP')

            # VLEIDINGL
            vleidingl_id = _guid()
            _add(entities, relations, vleidingl_id, 'VLEIDINGL', {
                'VLEIDINGL_AAN': '', 'VLEIDINGL_ARZ': '1', 'VLEIDINGL_ISO': '',
            })
            _link(relations, unit_rz_id, 'UNIT-RZ', vleidingl_id, 'VLEIDINGL')

            # BEGR-FORM voor hele UNIT-RZ
            unit_rz_begr_form_id = _guid()
            _add(entities, relations, unit_rz_begr_form_id, 'BEGR-FORM', {
                'BEGR-FORM_OPEN': 'true',
            })
            _link(relations, unit_rz_id, 'UNIT-RZ', unit_rz_begr_form_id, 'BEGR-FORM')

            # ── Verlichting (VERLZONE) ─────────────────────────────────────────
            for vl in rz.get('verlichtingen', []):
                vlzone_id = _guid()
                vl_ag = (vl['pct_opp'] / 100.0) * ag if ag > 0 else 0.0
                verlreg = REGELING_TO_VERLREG.get(vl['regeling'], 'VERLZ_VERLREG_CA')
                kag30   = vl.get('kag30', '0')
                kag30_val = 'VERLZ_KAG_KANT_WEL' if kag30 == '1' else 'VERLZ_KAG_KANT_NVT'
                _add(entities, relations, vlzone_id, 'VERLZONE', {
                    'VERLZ_A':       _fmt(vl_ag),
                    'VERLZ_DAGLREG': '',
                    'VERLZ_FD':      '',
                    'VERLZ_FD_NON':  '1,000',
                    'VERLZ_F_AFZ':   '0,00',
                    'VERLZ_KAG30':   kag30_val,
                    'VERLZ_OMSCHR':  vl['naam'],
                    'VERLZ_PN':      _fmt(vl['vermogen']),
                    'VERLZ_VERLREG': verlreg,
                    'VERLZ_WL':      '',
                })
                _link(relations, unit_rz_id, 'UNIT-RZ', vlzone_id, 'VERLZONE')

            # ── Geometrie (BEGR) ───────────────────────────────────────────────
            for hv_idx, hv in enumerate(rz.get('hoofdvlakken', [])):
                begr_id = _guid()
                locatie = hv['locatie']
                vlak = LOCATIE_TO_VLAK.get(locatie, 'VLAK_GEVEL')
                ori  = ORI_MAP.get(hv['orientatie'], 'Z')

                # BEGR_GEVEL alleen voor gevels
                begr_gevel = ori if vlak == 'VLAK_GEVEL' else ''
                begr_vloer = ''
                if vlak == 'VLAK_VLOER':
                    begr_vloer = 'VL_MV_GRSP'

                _add(entities, relations, begr_id, 'BEGR', {
                    'BEGR_A':          _fmt(hv['opp_bruto']),
                    'BEGR_AOR':        '',
                    'BEGR_AOS':        '',
                    'BEGR_B':          '',
                    'BEGR_DAK':        '',
                    'BEGR_DUMMY':      '',
                    'BEGR_GEVEL':      begr_gevel,
                    'BEGR_HEL':        'n.v.t.',
                    'BEGR_KWAND':      '',
                    'BEGR_L':          '',
                    'BEGR_OMSCHR':     hv['naam'],
                    'BEGR_OPM':        '',
                    'BEGR_VLAK':       vlak,
                    'BEGR_VLOER':      begr_vloer,
                    'BEGR_VLOER_BOVBUI': '',
                    'BEGR_VL_OMV':     '',
                }, order=100.0 + hv_idx)
                _link(relations, unit_rz_id, 'UNIT-RZ', begr_id, 'BEGR')
                _link(relations, gruimte_id, 'GRUIMTE', begr_id, 'BEGR')

                # BEGR auto-sub-entities
                for etype, props in [
                    ('CONSTRERROR', {'CONSTRERROR_LINCONSTR': '', 'CONSTRERROR_OPEN': 'true', 'CONSTRERROR_OPM': ''}),
                    ('CONSTRKENMV', {'KENMV_OMTR_VL': '0,00', 'KENMV_OPM': ''}),
                    ('CONSTRKENMW', {'KENMW_AFSTMV_VL': '', 'KENMW_OPM': ''}),
                    ('CONSTRKRVENT', {'KENMKR_OPM': '', 'KENMKR_VENT': '0,0012'}),
                    ('CONSTRWWGVL', {'KENMKR_WW_GVL': '', 'KENMKR_WW_GVL_OPM': ''}),
                    ('CONSTRWWKLDR', {'KENMKR_WW_KR': '', 'KENMKR_WW_KR_OPM': ''}),
                ]:
                    sub_id = _guid()
                    _add(entities, relations, sub_id, etype, props)
                    _link(relations, begr_id, 'BEGR', sub_id, etype)

                # CONSTRWG (opaque wandgedeelte)
                constrwg_id = _guid()
                _add(entities, relations, constrwg_id, 'CONSTRWG', {
                    'CONSTRWG_B':   '',
                    'CONSTRWG_L':   '',
                    'CONSTRWG_LIB': '',
                    'CONSTRWG_OPM': '',
                    'CONSTRWG_OPP': _fmt(hv['opp_netto']),
                })
                _link(relations, begr_id, 'BEGR', constrwg_id, 'CONSTRWG')

                # CONSTRD – opaque constructie (1 per BEGR)
                constrd_id = _guid()
                # Maak LIBCONSTRD voor deze opaque constructie
                lib_key = hv['constr_guid'] or f'rc_{hv["rc"]:.2f}_{locatie}'
                if lib_key not in lib_constrd_map:
                    lcd_id = _guid()
                    libtype = LOCATIE_TO_LIBCONSTRD.get(locatie, 'LIBVLAK_GEVEL')
                    # Override met constructietype indien bekend
                    c_info = vabi['constrs'].get(hv['constr_guid'], {})
                    ctype = c_info.get('type', '0')
                    libtype = CTYPE_TO_LIBCONSTRD.get(ctype, libtype)

                    _add(entities, relations, lcd_id, 'LIBCONSTRD', {
                        'LIBCONSTRD_BEPALING': 'LIBCONSTRD_BEPALING_41',
                        'LIBCONSTRD_DIKTE_ISO': 'n.v.t.',
                        'LIBCONSTRD_DIKTE_RIET': 'n.v.t.',
                        'LIBCONSTRD_METH':   'BESLISS',
                        'LIBCONSTRD_OMSCHR': c_info.get('naam', hv['naam']),
                        'LIBCONSTRD_RC':     _fmt(hv['rc']),
                        'LIBCONSTRD_TYPE':   libtype,
                    })
                    lib_constrd_map[lib_key] = lcd_id

                lcd_id = lib_constrd_map[lib_key]
                _add(entities, relations, constrd_id, 'CONSTRD', {
                    'CONSTRD_B':   '',
                    'CONSTRD_L':   '',
                    'CONSTRD_LIB': lcd_id,
                    'CONSTRD_OPM': '',
                    'CONSTRD_OPP': _fmt(hv['opp_netto']),
                })
                _link(relations, begr_id, 'BEGR', constrd_id, 'CONSTRD')
                _link(relations, lcd_id, 'LIBCONSTRD', constrd_id, 'CONSTRD')

                # CONSTRL (lineaire koudebruggen – leeg)
                constrl_id = _guid()
                _add(entities, relations, constrl_id, 'CONSTRL', {
                    'CONSTRL_LEN': '',
                    'CONSTRL_LIB': libconstrl_id,
                    'CONSTRL_OPM': '',
                })
                _link(relations, begr_id, 'BEGR', constrl_id, 'CONSTRL')
                _link(relations, libconstrl_id, 'LIBCONSTRL', constrl_id, 'CONSTRL')

                # ── Deelvlakken → CONSTRT ──────────────────────────────────────
                for dv in hv.get('deelvlakken', []):
                    c_type = dv.get('c_type', '2')
                    if c_type not in ('2', '3'):
                        continue  # alleen ramen en deuren

                    # LIBCONSTRT (per unieke U/g combinatie + type)
                    is_raam = (c_type == '2')
                    trans_type = 'TRANSTYPE_RAAM' if is_raam else 'TRANSTYPE_DEUR'
                    lct_key = (round(dv['u'], 2), round(dv['g'], 2), trans_type)
                    if lct_key not in lib_constrt_map:
                        lct_id = _guid()
                        _add(entities, relations, lct_id, 'LIBCONSTRT', {
                            'LIBCONSTRT_AC':       '',
                            'LIBCONSTRT_BEPALING': 'LIBCONSTRT_BEPALING_NVT',
                            'LIBCONSTRT_G':        _fmt(dv['g']),
                            'LIBCONSTRT_KOZ':      'LIBCONSTRT_KOZ_NVT',
                            'LIBCONSTRT_METH':     'TRANS_VRIJE_INV',
                            'LIBCONSTRT_OMSCHR':   dv['c_naam'],
                            'LIBCONSTRT_TYPE':     trans_type,
                            'LIBCONSTRT_U':        _fmt(dv['u']),
                        })
                        lib_constrt_map[lct_key] = lct_id

                    lct_id = lib_constrt_map[lct_key]

                    # Afmetingen: als B=0 en H=0, gebruik opp als L en B=1
                    dv_b = dv['b']
                    dv_h = dv['h']
                    if dv_b == 0.0 or dv_h == 0.0:
                        dv_h = dv['opp']
                        dv_b = 1.0

                    constrt_id = _guid()
                    _add(entities, relations, constrt_id, 'CONSTRT', {
                        'CONSTRT_AANT':   '1',
                        'CONSTRT_B':      _fmt(dv_b),
                        'CONSTRT_BESCH':  dv['besch'],
                        'CONSTRT_GGL_ALT': '',
                        'CONSTRT_GGL_DIF': '',
                        'CONSTRT_L':      _fmt(dv_h),
                        'CONSTRT_LIB':    lct_id,
                        'CONSTRT_OPM':    '',
                        'CONSTRT_OPP':    _fmt(dv['opp']),
                        'CONSTRT_REGEL':  '',
                        'CONSTRT_ZNVENT': 'ZOMERNVENT_NAANW',
                        'CONSTRT_ZONW':   'ZONW_GEEN',
                    })
                    _link(relations, begr_id, 'BEGR', constrt_id, 'CONSTRT')
                    _link(relations, lct_id, 'LIBCONSTRT', constrt_id, 'CONSTRT')

                    # CONSTRZOMNAC (per CONSTRT)
                    zomnac_id = _guid()
                    _add(entities, relations, zomnac_id, 'CONSTRZOMNAC', {
                        'CONSTRZOMNAC_DOORLF':  '0,30',
                        'CONSTRZOMNAC_DOORLV':  '',
                        'CONSTRZOMNAC_INV':     '',
                    })
                    _link(relations, constrt_id, 'CONSTRT', zomnac_id, 'CONSTRZOMNAC')

                    # BELEMMERING (als aanwezig)
                    if dv['besch'] != 'n.v.t.':
                        belem_id = _guid()
                        heeft_r = dv['besch'] in ('RECHTS', 'BEIDE')
                        heeft_l = dv['besch'] in ('LINKS',  'BEIDE')
                        _add(entities, relations, belem_id, 'BELEMMERING', {
                            'BELEMM_CONST_BELEM':    '',
                            'BELEMM_HOR_A_RECHTS':   _fmt(dv['r_a']) if heeft_r else '',
                            'BELEMM_HOR_B_RECHTS':   _fmt(dv['r_b']) if heeft_r else '',
                            'BELEMM_HOR_A_LINKS':    _fmt(dv['l_a']) if heeft_l else '',
                            'BELEMM_HOR_B_LINKS':    _fmt(dv['l_b']) if heeft_l else '',
                        })
                        _link(relations, constrt_id, 'CONSTRT', belem_id, 'BELEMMERING')

            # ── TAPW-UNIT-RZ ───────────────────────────────────────────────────
            tapw_unit_rz_id = _guid()
            _add(entities, relations, tapw_unit_rz_id, 'TAPW-UNIT-RZ', {
                'TAPW-UNIT-RZ_ID': unit_rz_id,
            })
            _link(relations, unit_rz_id, 'UNIT-RZ', tapw_unit_rz_id, 'TAPW-UNIT-RZ')

            # ── Installatiesystemen ─────────────────────────────────────────────
            _build_verw(entities, relations, unit_rz_id, rz_id)
            _build_vent(entities, relations, unit_rz_id, rz_id, ventcap_id)
            if rz.get('koeling'):
                _build_koel(entities, relations, unit_rz_id, rz_id)
            _build_tapw(entities, relations, unit_rz_id, tapw_unit_rz_id)
            _build_verl(entities, relations)

    # ── PRESTATIE (gebouwniveau) ────────────────────────────────────────────────
    prestatie_geb_id = _guid()
    _add(entities, relations, prestatie_geb_id, 'PRESTATIE', {
        'EP_BENG1': '', 'EP_BENG2': '', 'EP_BENG3': '',
        'EP_ENERGIELABEL': '',
    })
    _link(relations, basis_id, 'BASIS', prestatie_geb_id, 'PRESTATIE')

    return entities, relations


def _build_verw(entities, relations, unit_rz_id, rz_id):
    """Verwarming installatie met forfaitaire defaults."""
    inst_id = _guid()
    _add(entities, relations, inst_id, 'INSTALLATIE', {
        'INSTALL_AANTAL': '1',
        'INSTALL_NAAM':   'Verwarming 1',
        'INSTALL_OMSCHR': '',
        'INSTALL_TYPE':   'INST_VERW',
    })

    verw_id = _guid()
    _add(entities, relations, verw_id, 'VERW', {
        'VERW_OPEN':     'true',
        'VERW_OPM':      '',
        'VERW_VAT_AANW': '',
    })
    _link(relations, inst_id, 'INSTALLATIE', verw_id, 'VERW')

    # RZ link (VERW → RZ → UNIT-RZ)
    _link(relations, verw_id, 'VERW', rz_id, 'RZ')
    _link(relations, rz_id, 'RZ', unit_rz_id, 'UNIT-RZ')

    # VERW-OPWEK
    opwek_id = _guid()
    _add(entities, relations, opwek_id, 'VERW-OPWEK', {
        'VERW-OPWEK_FABR':   'VERW-OPWEK_FABR_A',
        'VERW-OPWEK_FUNCTIE': 'VERW-OPWEK_FUNCTIE_V',
        'VERW-OPWEK_GEM':    'VERW-OPWEK_GEM_NIET',
        'VERW-OPWEK_INVOER': 'VERW-OPWEK_INVOER_FORF',
        'VERW-OPWEK_POMP':   'VERW-OPWEK_POMP_BINN',
        'VERW-OPWEK_TYPE':   'VERW-OPWEK_TYPE_A',
        'VERW-OPWEK_TOE_AAN': '1',
    })
    _link(relations, verw_id, 'VERW', opwek_id, 'VERW-OPWEK')

    # VERW-AFG
    afg_id = _guid()
    _add(entities, relations, afg_id, 'VERW-AFG', {
        'VERW-AFG_TYPE_AFG':  'VERW-AFG_TYPE_AFG_VLV',
        'VERW-AFG_TYPE_RUIM': 'VERW-AFG_TYPE_RUIM_65',
        'VERW-AFG_VERT':      'VERW-AFG_VERT_E',
    })
    _link(relations, verw_id, 'VERW', afg_id, 'VERW-AFG')
    _link(relations, rz_id, 'RZ', afg_id, 'VERW-AFG')

    for _ in range(2):
        afg_vent_id = _guid()
        _add(entities, relations, afg_vent_id, 'VERW-AFG-VENT', {
            'VERW-AFG-VENT_INV': 'VERW-AFG-VENT_INV_GEEN',
            'VERW-AFG-VENT_SRT': 'VERW-AFG-VENT_SRT_NVT',
        })
        _link(relations, afg_id, 'VERW-AFG', afg_vent_id, 'VERW-AFG-VENT')

    # VERW-DISTR
    distr_id = _guid()
    _add(entities, relations, distr_id, 'VERW-DISTR', {
        'VERW-DISTR_AANV_POMP':     'VERW-DISTR_AANV_POMP_WEL',
        'VERW-DISTR_AAN_LAGEN':     '2',
        'VERW-DISTR_ONTW':          'VERW-DISTR_ONTW_GE32_D',
        'VERW-DISTR_POMP_INV':      'VERW-DISTR_POMP_INV_D',
        'VERW-DISTR_REG_AANVTEMP':  'VERW-DISTR_REG_AANVTEMP_STOOKLIJN',
        'VERW-DISTR_TYPE':          'VERW-DISTR_TYPE_C',
        'VERW-DISTR_WAT':           'VERW-DISTR_WAT_W',
        'VERW-DISTR_FUNCTIE_LEID':  'VERW-DISTR_FUNCTIE_LEID_VERW',
    })
    _link(relations, verw_id, 'VERW', distr_id, 'VERW-DISTR')

    for side, inv in [('BIN', 'VERW-DISTR-BIN_INV_E'), ('BUI', 'VERW-DISTR-BUI_INV_G')]:
        side_id = _guid()
        _add(entities, relations, side_id, f'VERW-DISTR-{side}', {
            f'VERW-DISTR-{side}_INV':     inv,
            f'VERW-DISTR-{side}_ISO_KLE': '' if side == 'BUI' else 'VERW-DISTR-BIN_KLEP_WEL',
            f'VERW-DISTR-{side}_ISO_LEI': 'VERW-DISTR-_ISO_LEI_G',
            f'VERW-DISTR-{side}_LEN':     '',
        })
        _link(relations, distr_id, 'VERW-DISTR', side_id, f'VERW-DISTR-{side}')

        eig_id = _guid()
        _add(entities, relations, eig_id, 'VERW-DISTR-EIG', {
            'VERW-DISTR-EIG_DEK':     'n.v.t.',
            'VERW-DISTR-EIG_LAB_CON': 'n.v.t.',
            'VERW-DISTR-EIG_LAB_ISO': 'n.v.t.',
            'VERW-DISTR-EIG_RUIMTE':  'binnen verwarmde zone',
        })
        _link(relations, side_id, f'VERW-DISTR-{side}', eig_id, 'VERW-DISTR-EIG')

    pomp_id = _guid()
    _add(entities, relations, pomp_id, 'VERW-DISTR-POMP', {
        'VERW-DISTR_POMP_OMSCHR': 'pomp 1',
    })
    _link(relations, distr_id, 'VERW-DISTR', pomp_id, 'VERW-DISTR-POMP')

    # VERW-VAT
    vat_id = _guid()
    _add(entities, relations, vat_id, 'VERW-VAT', {
        'VERW-VAT_AANT': '1',
    })
    _link(relations, verw_id, 'VERW', vat_id, 'VERW-VAT')


def _build_vent(entities, relations, unit_rz_id, rz_id, ventcap_id):
    """Ventilatie installatie (forfaitaire methode)."""
    inst_id = _guid()
    _add(entities, relations, inst_id, 'INSTALLATIE', {
        'INSTALL_AANTAL': '1',
        'INSTALL_NAAM':   'Ventilatie 1',
        'INSTALL_OMSCHR': '',
        'INSTALL_TYPE':   'INST_VENT',
    })

    vent_id = _guid()
    _add(entities, relations, vent_id, 'VENT', {
        'VENT_OPEN': 'true',
        'VENT_OPM':  '',
    })
    _link(relations, inst_id, 'INSTALLATIE', vent_id, 'VENT')
    _link(relations, vent_id, 'VENT', rz_id, 'RZ')

    # VENTAAN (forfaitaire methode)
    ventaan_id = _guid()
    _add(entities, relations, ventaan_id, 'VENTAAN', {
        'VENTAAN_FCTRL':   '',
        'VENTAAN_INVOER':  'VENT_FORF',
        'VENTAAN_SYS':     '',
        'VENTAAN_SYSVAR':  '',
        'VENTAAN_VARIANT': '',
        'VENTAAN_VERB':    '',
        'VENTAAN_VERBL':   '',
    })
    _link(relations, vent_id, 'VENT', ventaan_id, 'VENTAAN')

    # VENTILATOR + VENTILATOREIG
    for i in range(2):
        ventilator_id = _guid()
        _add(entities, relations, ventilator_id, 'VENTILATOR', {})
        _link(relations, ventaan_id, 'VENTAAN', ventilator_id, 'VENTILATOR')

        veig_id = _guid()
        _add(entities, relations, veig_id, 'VENTILATOREIG', {})
        _link(relations, ventilator_id, 'VENTILATOR', veig_id, 'VENTILATOREIG')

    # WARMTETERUG + WARMTE-TOEV-KAN
    wtr_id = _guid()
    _add(entities, relations, wtr_id, 'WARMTETERUG', {})
    _link(relations, ventaan_id, 'VENTAAN', wtr_id, 'WARMTETERUG')

    wtk_id = _guid()
    _add(entities, relations, wtk_id, 'WARMTE-TOEV-KAN', {})
    _link(relations, wtr_id, 'WARMTETERUG', wtk_id, 'WARMTE-TOEV-KAN')

    # VENTDIS, VENTDEB, VENTCAP (vent), VENTZBR
    ventdis_id = _guid()
    _add(entities, relations, ventdis_id, 'VENTDIS', {
        'VENTDIS_C':     'VENTDIS_C_BUI',
        'VENTDIS_CKOEL': 'VENTDIS_CKOEL_GEEN',
        'VENTDIS_CVERW': 'VENTDIS_CVERW_GEEN',
        'VENTDIS_DICHT': 'VENTDIS_DICHT_ONB',
        'VENTDIS_LBK':   'VENTDIS_LBK_D_A',
    })
    _link(relations, vent_id, 'VENT', ventdis_id, 'VENTDIS')

    ventdeb_id = _guid()
    _add(entities, relations, ventdeb_id, 'VENTDEB', {
        'VENTDEB_CAP':    'VENTDEBCAP_ONB',
        'VENTDEB_CAPTAB': '',
    })
    _link(relations, vent_id, 'VENT', ventdeb_id, 'VENTDEB')

    ventcap2_id = _guid()
    _add(entities, relations, ventcap2_id, 'VENTCAP', {
        'VENTCAP_MD': '', 'VENTCAP_MV': '', 'VENTCAP_NAOS': '',
    })
    _link(relations, vent_id, 'VENT', ventcap2_id, 'VENTCAP')

    ventzbr_id = _guid()
    _add(entities, relations, ventzbr_id, 'VENTZBR', {
        'VENTZBR_AANW': 'False',
        'VENTZBR_AG':   '',
    })
    _link(relations, vent_id, 'VENT', ventzbr_id, 'VENTZBR')
    _link(relations, rz_id, 'RZ', ventzbr_id, 'VENTZBR')

    # VENT-VERB x2
    for _ in range(2):
        verb_id = _guid()
        _add(entities, relations, verb_id, 'VENT-VERB', {})
        _link(relations, vent_id, 'VENT', verb_id, 'VENT-VERB')

    # VOORWARM
    voorwarm_id = _guid()
    _add(entities, relations, voorwarm_id, 'VOORWARM', {
        'VOORWARM_AAN': '',
    })
    _link(relations, vent_id, 'VENT', voorwarm_id, 'VOORWARM')


def _build_koel(entities, relations, unit_rz_id, rz_id):
    """Koeling installatie (forfaitaire defaults)."""
    inst_id = _guid()
    _add(entities, relations, inst_id, 'INSTALLATIE', {
        'INSTALL_AANTAL': '1',
        'INSTALL_NAAM':   'Koeling 1',
        'INSTALL_OMSCHR': '',
        'INSTALL_TYPE':   'INST_KOEL',
    })

    koel_id = _guid()
    _add(entities, relations, koel_id, 'KOEL', {
        'KOEL_OPEN': 'true',
        'KOEL_OPM':  '',
    })
    _link(relations, inst_id, 'INSTALLATIE', koel_id, 'KOEL')
    _link(relations, koel_id, 'KOEL', rz_id, 'RZ')

    # KOEL-OPWEK
    opwek_id = _guid()
    _add(entities, relations, opwek_id, 'KOEL-OPWEK', {
        'KOEL-OPWEK_FABR':    'KOEL-OPWEK_FABR_GR',
        'KOEL-OPWEK_GEM':     'KOEL-OPWEK_GEM_NIET',
        'KOEL-OPWEK_INVOER':  'VERW-OPWEK_INVOER_FORF',
        'KOEL-OPWEK_TYPE':    'KOEL-OPWEK_TYPE_1',
        'KOEL-OPWEK_WCCTRLEN': 'false',
        'KOEL-OPWEK_WCGEN_INV_FORF': 'true',
        'KOEL-OPWEK_FCGEN_INV_FORF': 'True',
    })
    _link(relations, koel_id, 'KOEL', opwek_id, 'KOEL-OPWEK')

    # KOEL-AFG
    afg_id = _guid()
    _add(entities, relations, afg_id, 'KOEL-AFG', {
        'KOEL-AFG_TYPE_AFG':  'KOEL-AFG_TYPE_AFG_6',
        'KOEL-AFG_TYPE_RUIM': 'KOEL-AFG_TYPE_RUIM_9',
    })
    _link(relations, koel_id, 'KOEL', afg_id, 'KOEL-AFG')
    _link(relations, rz_id, 'RZ', afg_id, 'KOEL-AFG')

    for _ in range(2):
        afg_vent_id = _guid()
        _add(entities, relations, afg_vent_id, 'KOEL-AFG-VENT', {
            'KOEL-AFG-VENT_INV': 'VERW-AFG-VENT_INV_GEEN',
        })
        _link(relations, afg_id, 'KOEL-AFG', afg_vent_id, 'KOEL-AFG-VENT')
    _link(relations, rz_id, 'RZ', afg_vent_id, 'KOEL-AFG-VENT')

    # KOEL-DISTR
    distr_id = _guid()
    _add(entities, relations, distr_id, 'KOEL-DISTR', {
        'KOEL-DISTR_AAN_LAGEN': '2',
        'KOEL-DISTR_ONTW':      'KOEL-DISTR_ONTW_4',
        'KOEL-DISTR_POMP_INV':  'VERW-DISTR_POMP_INV_D',
        'KOEL-DISTR_VERDAMP':   'KOEL-DISTR_VERDAMP_3',
        'KOEL-DISTR_WAT':       'KOEL-DISTR_WAT_6',
        'KOEL-DISTR_WCAUX':     'false',
    })
    _link(relations, koel_id, 'KOEL', distr_id, 'KOEL-DISTR')

    bui_id = _guid()
    _add(entities, relations, bui_id, 'KOEL-DISTR-BUI', {
        'KOEL-DISTR-BUI_INV':     'VERW-DISTR-BUI_INV_H',
        'KOEL-DISTR-BUI_ISO_LEI': 'VERW-DISTR-_ISO_LEI_G',
    })
    _link(relations, distr_id, 'KOEL-DISTR', bui_id, 'KOEL-DISTR-BUI')

    eig_id = _guid()
    _add(entities, relations, eig_id, 'KOEL-DISTR-EIG', {
        'KOEL-DISTR-EIG_DEK':     'n.v.t.',
        'KOEL-DISTR-EIG_LAB_CON': 'n.v.t.',
        'KOEL-DISTR-EIG_LAB_ISO': 'n.v.t.',
        'KOEL-DISTR-EIG_RUIMTE':  'buiten gekoelde zone',
    })
    _link(relations, bui_id, 'KOEL-DISTR-BUI', eig_id, 'KOEL-DISTR-EIG')

    pomp_id = _guid()
    _add(entities, relations, pomp_id, 'KOEL-DISTR-POMP', {
        'KOEL-DISTR_POMP_OMSCHR': 'pomp 1',
    })
    _link(relations, distr_id, 'KOEL-DISTR', pomp_id, 'KOEL-DISTR-POMP')


def _build_tapw(entities, relations, unit_rz_id, tapw_unit_rz_id):
    """Tapwater installatie (forfaitaire defaults)."""
    inst_id = _guid()
    _add(entities, relations, inst_id, 'INSTALLATIE', {
        'INSTALL_AANTAL': '1',
        'INSTALL_NAAM':   'Tapwater 1',
        'INSTALL_OMSCHR': '',
        'INSTALL_TYPE':   'INST_TAPW',
    })

    tapw_id = _guid()
    _add(entities, relations, tapw_id, 'TAPW', {
        'TAPW_OPEN': 'true',
        'TAPW_OPM':  '',
    })
    _link(relations, inst_id, 'INSTALLATIE', tapw_id, 'TAPW')

    # TAPW-OPWEK
    opwek_id = _guid()
    _add(entities, relations, opwek_id, 'TAPW-OPWEK', {
        'TAPW-OPWEK_INVOER': 'TAPW-OPWEK_INVOER_FORF',
        'TAPW-OPWEK_TYPE':   'TAPW-OPWEK_TYPE_A',
        'TAPW-OPWEK_FUNCT':  'TAPW-OPWEK_FUNCT_V',
        'TAPW-OPWEK_GEM':    'TAPW-OPWEK_GEM_NIET',
    })
    _link(relations, tapw_id, 'TAPW', opwek_id, 'TAPW-OPWEK')

    # TAPW-AFG
    afg_id = _guid()
    _add(entities, relations, afg_id, 'TAPW-AFG', {
        'TAPW-AFG_TYPE': 'TAPW-AFG_TYPE_KRAAN',
    })
    _link(relations, tapw_id, 'TAPW', afg_id, 'TAPW-AFG')
    _link(relations, tapw_id, 'TAPW', tapw_unit_rz_id, 'TAPW-UNIT-RZ')

    # TAPW-DISTR
    distr_id = _guid()
    _add(entities, relations, distr_id, 'TAPW-DISTR', {
        'TAPW-DISTR_AANV_POMP': '',
        'TAPW-DISTR_TYPE':      'TAPW-DISTR_TYPE_A',
    })
    _link(relations, tapw_id, 'TAPW', distr_id, 'TAPW-DISTR')

    for side, inv in [('BIN', 'TAPW-DISTR-BIN_INV_A'), ('BUI', 'TAPW-DISTR-BUI_INV_A')]:
        side_id = _guid()
        _add(entities, relations, side_id, f'TAPW-DISTR-{side}', {
            f'TAPW-DISTR-{side}_INV': inv,
        })
        _link(relations, distr_id, 'TAPW-DISTR', side_id, f'TAPW-DISTR-{side}')

        eig_id = _guid()
        _add(entities, relations, eig_id, 'TAPW-DISTR-EIG', {
            'TAPW-DISTR-EIG_RUIMTE': 'binnen verwarmde zone',
        })
        _link(relations, side_id, f'TAPW-DISTR-{side}', eig_id, 'TAPW-DISTR-EIG')

    pomp_id = _guid()
    _add(entities, relations, pomp_id, 'TAPW-DISTR-POMP', {
        'TAPW-DISTR_POMP_OMSCHR': 'pomp 1',
    })
    _link(relations, distr_id, 'TAPW-DISTR', pomp_id, 'TAPW-DISTR-POMP')

    # TAPW-VAT
    vat_id = _guid()
    _add(entities, relations, vat_id, 'TAPW-VAT', {
        'TAPW-VAT_AANT': '1',
    })
    _link(relations, tapw_id, 'TAPW', vat_id, 'TAPW-VAT')


def _build_verl(entities, relations):
    """Verlichtings-INSTALLATIE (koppelt VERLZONEs)."""
    inst_id = _guid()
    _add(entities, relations, inst_id, 'INSTALLATIE', {
        'INSTALL_AANTAL': '1',
        'INSTALL_NAAM':   'Verlichting 1',
        'INSTALL_OMSCHR': '',
        'INSTALL_TYPE':   'INST_VERL',
    })

    verl_id = _guid()
    _add(entities, relations, verl_id, 'VERL', {
        'VERL_DAGLREG':      'VERL_DAGREG_GEEN',
        'VERL_OPEN':         'true',
        'VERL_PARVERM_INV':  'VERL_VERMP_FORF',
        'VERL_VERM_INV':     'VERL_VERM_EW',
    })
    _link(relations, inst_id, 'INSTALLATIE', verl_id, 'VERL')


def _build_installations_form(entities, relations):
    """INSTALLATIONS-FORM (globaal)."""
    form_id = _guid()
    _add(entities, relations, form_id, 'INSTALLATIONS-FORM', {
        'INSTALLATIONS-FORM_OPEN': 'true',
    })


# ─── HOOFD CONVERTER ──────────────────────────────────────────────────────────

def convert(epa_bytes, filename='import'):
    """
    Converteer een VABI EPA bestand naar een Uniec3 bestand.

    Parameters
    ----------
    epa_bytes : bytes
        De bytes van het .epa bestand.
    filename : str
        Bestandsnaam (zonder extensie) voor de omschrijving.

    Returns
    -------
    bytes
        De bytes van het gegenereerde .uniec3 bestand.
    """
    global _BUILD_ID
    _BUILD_ID = abs(hash(filename)) % 9_000_000 + 1_000_000

    vabi = _read_vabi(epa_bytes)
    if not vabi.get('naam') or vabi['naam'] == 'VABI import':
        vabi['naam'] = filename

    entities, relations = _build_entities(vabi)
    _build_installations_form(entities, relations)

    # Schrijf Uniec3 ZIP
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        # meta.json
        meta = {
            'Version': 2,
            'App': 'NTA8800, Version=3.4.1.0, Culture=neutral, PublicKeyToken=null',
            'ExportedBy': _guid(),
            'ExportedOn': _now(),
            'RootFolderId': 1,
            'Environment': 'app.uniec3.nl:443',
        }
        z.writestr('meta.json', json.dumps(meta, ensure_ascii=False))

        # folders.json
        folders = [{'FolderId': 1, 'ParentId': 0, 'ProjectId': 1, 'Name': 'VABI import'}]
        z.writestr('folders.json', json.dumps(folders, ensure_ascii=False))

        # projects.json
        projects = [{
            'ProjectId': 1,
            'FolderId':  1,
            'Name':      filename,
            'Order':     0,
            'Change':    0,
            'CreateDate': _now(),
            'LastOpenDate': _now(),
        }]
        z.writestr('projects.json', json.dumps(projects, ensure_ascii=False))

        # buildings.json
        buildings = [{
            'BuildingId':    _BUILD_ID,
            'ProjectId':     1,
            'NTAVersionId':  312,
            'Locked':        False,
            'Afgemeld':      False,
            'CreateDate':    _now(),
            'ChangeDate':    _now(),
        }]
        z.writestr('buildings.json', json.dumps(buildings, ensure_ascii=False))

        # entities.json + relations.json
        bid = _BUILD_ID
        z.writestr(f'buildings/{bid}/entities.json',
                   json.dumps(entities, ensure_ascii=False, indent=2))
        z.writestr(f'buildings/{bid}/relations.json',
                   json.dumps(relations, ensure_ascii=False, indent=2))

    return buf.getvalue()
