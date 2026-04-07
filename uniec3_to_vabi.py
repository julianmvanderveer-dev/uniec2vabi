"""
uniec3_to_vabi.py — Converteert een .uniec3 bestand naar een VABI EPA .epa bestand.

.uniec3  = ZIP met buildings/{id}/entities.json + relations.json
.epa     = ZIP met project.xml  (VABI EPA formaat 11.x)
"""

import io
import json
import uuid
import zipfile
from collections import defaultdict
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom

# ─── CONSTANTEN ───────────────────────────────────────────────────────────────

_ZERO_GUID = '00000000-0000-0000-0000-000000000000'

VLAK_TO_LOCATIE = {
    'VLAK_VLOER':        '6',
    'VLAK_GEVEL':        '2',
    'VLAK_DAK':          '1',
    'VLAK_VLOER_BOVBUI': '7',
    'VLAK_KASW':         '2',
    'VLAK_WONSCHEID':    '2',
}

_OMSCHR_LOCATIE = [
    ('voor', '2'), ('vg', '2'),
    ('acht', '3'), ('ag', '3'),
    ('link', '4'), ('lzg', '4'),
    ('recht', '5'), ('rzg', '5'),
    ('dak', '1'), ('roof', '1'),
    ('vloer', '6'), ('floor', '6'),
]

_GEVEL_ORI = {
    'Z': '0', 'ZW': '1', 'W': '2', 'NW': '3',
    'N': '4', 'NO': '5', 'O': '6', 'ZO': '7',
}

_DEFAULT_ORI = {
    '2': '0', '3': '4', '4': '2', '5': '6',
    '1': '0', '6': '0', '7': '0',
}

# Compass-richting → VABI locatie (voor/achter/links/rechts gevel)
_GEVEL_COMPASS_TO_LOCATIE = {
    'Z':  '2', 'ZW': '2', 'ZO': '2',
    'N':  '3', 'NW': '3', 'NO': '3',
    'W':  '4',
    'O':  '5',
}

_HELLING = {
    '2': '6', '3': '6', '4': '6', '5': '6',
    '1': '0', '6': '0', '7': '0',
}

LIBCONSTRD_TO_TYPE = {
    'LIBVLAK_GEVEL': '0', 'LIBVLAK_DAK': '6', 'LIBVLAK_VLOER': '7',
    'LIBVLAK_WONSCHEID': '1', 'LIBVLAK_KASW': '1', 'LIBVLAK_BUI': '0',
}

LIBCONSTRT_TO_TYPE = {
    'TRANSTYPE_RAAM': '2', 'TRANSTYPE_DEUR': '3',
    'TRANSTYPE_ZRK': '2', 'TRANSTYPE_DR': '3',
}

VENT_SYS_MAP = {
    'VENTSYS_NAT': '1', 'VENTSYS_MECHA': '2', 'VENTSYS_MECHB': '2',
    'VENTSYS_MECHC': '3', 'VENTSYS_MECHD': '4', 'VENTSYS_WTW': '4',
}

# VERW-OPWEK_TYPE_* (oud formaat) → VABI 11.x TypeOpwekker waarden
# 1=HR condenserend gas, 2=niet-condenserend, 3=WKK, 9=warmtepomp, 10=stadsverwarming
VERW_TYPE_MAP = {
    'VERW-OPWEK_TYPE_A': '1',  'VERW-OPWEK_TYPE_B': '1',
    'VERW-OPWEK_TYPE_C': '2',  'VERW-OPWEK_TYPE_D': '10',
    'VERW-OPWEK_TYPE_E': '10', 'VERW-OPWEK_TYPE_F': '10',
    'VERW-OPWEK_TYPE_G': '9',  'VERW-OPWEK_TYPE_H': '9',
}

# VERW-OPWEK_FABR_* (werkelijk formaat in .uniec3) → VABI 11.x TypeOpwekker waarden
VERW_FABR_MAP = {
    'VERW-OPWEK_FABR_A': '1',   # HR condenserende ketel gas
    'VERW-OPWEK_FABR_B': '1',   # HR ketel (alternatief)
    'VERW-OPWEK_FABR_C': '2',   # Niet-condenserende ketel
    'VERW-OPWEK_FABR_D': '10',  # Stadsverwarming
    'VERW-OPWEK_FABR_E': '9',   # Warmtepomp elektrisch
    'VERW-OPWEK_FABR_F': '9',   # Warmtepomp gas
    'VERW-OPWEK_FABR_G': '3',   # WKK
    'VERW-OPWEK_FABR_H': '1',   # Overig HR
}

# TAPW-OPWEK_TYPE_* (oud formaat) → VABI 11.x TypeToestel waarden
# 1=combi-ketel/geiser gas, 3=elektrisch, 4=warmtepomp, 5=stadsverwarming
TAPW_TYPE_MAP = {
    'TAPW-OPWEK_TYPE_1': '1', 'TAPW-OPWEK_TYPE_2': '1',
    'TAPW-OPWEK_TYPE_3': '1', 'TAPW-OPWEK_TYPE_4': '1',
    'TAPW-OPWEK_TYPE_5': '4',  # warmtepomp
}

# TAPW-OPWEK_FABR_* (werkelijk formaat in .uniec3) → VABI 11.x TypeToestel waarden
TAPW_FABR_MAP = {
    'TAPW-OPWEK_FABR_A': '1',  # Combi-ketel gas
    'TAPW-OPWEK_FABR_B': '1',  # Doorstroomtoestel gas
    'TAPW-OPWEK_FABR_C': '1',  # HR combi-ketel
    'TAPW-OPWEK_FABR_D': '3',  # Elektrische boiler
    'TAPW-OPWEK_FABR_E': '4',  # Warmtepomp
    'TAPW-OPWEK_FABR_F': '1',  # Overige
}

# ─── HULPFUNCTIES ─────────────────────────────────────────────────────────────

def _guid():
    return str(uuid.uuid4())


def _prop(entity: dict, prop_id: str, default: str = '') -> str:
    for p in entity.get('NTAPropertyDatas', []):
        if p.get('NTAPropertyId') == prop_id:
            v = p.get('Value', '') or ''
            if v.strip():
                return v.strip()
    return default


def _num(entity: dict, prop_id: str) -> float | None:
    raw = _prop(entity, prop_id)
    if not raw:
        return None
    try:
        return float(raw.replace(',', '.'))
    except ValueError:
        return None


def _fmt(val) -> str:
    if val is None:
        return '0'
    return str(round(float(val), 4))


def _omschr_to_locatie(omschr: str) -> str | None:
    s = omschr.lower()
    for kw, loc in _OMSCHR_LOCATIE:
        if kw in s:
            return loc
    return None


def _gevel_to_ori(begr_gevel: str) -> str | None:
    if not begr_gevel:
        return None
    parts = begr_gevel.split('_')
    if parts:
        last = parts[-1].upper()
        if last in _GEVEL_ORI:
            return _GEVEL_ORI[last]
    return None


def _xml_text(parent: Element, tag: str, text: str) -> Element:
    el = SubElement(parent, tag)
    el.text = text
    return el


def _xml_empty(parent: Element, tag: str) -> Element:
    return SubElement(parent, tag)


def _xml_list(parent: Element, tag: str) -> Element:
    """Maakt een lijst-container met Index='-1' en Guid 00000000."""
    el = SubElement(parent, tag)
    el.set('Index', '-1')
    _xml_text(el, 'Guid', _ZERO_GUID)
    return el


def _pretty(root: Element) -> bytes:
    """Formatteer XML. VABI schrijft geen XML-declaratie."""
    raw = tostring(root, encoding='unicode')
    dom = minidom.parseString(raw)
    pretty = dom.toprettyxml(indent='\t')
    lines = pretty.split('\n')
    if lines and lines[0].startswith('<?xml'):
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines = lines[1:]
    return '\n'.join(lines).encode('utf-8')


# ─── PARSER ───────────────────────────────────────────────────────────────────

class Uniec3Data:
    def __init__(self, zip_bytes: bytes):
        self.entities_by_id: dict[str, dict] = {}
        self.entities_by_type: dict[str, list] = defaultdict(list)
        self.children_of: dict[str, list] = defaultdict(list)
        self.parents_of: dict[str, list] = defaultdict(list)
        self.building_id: int = 0
        self._load(zip_bytes)

    def _load(self, zip_bytes: bytes):
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            e_path = r_path = None
            for n in sorted(names):
                if '/entities.json' in n and 'buildings/' in n:
                    e_path = n
                if '/relations.json' in n and 'buildings/' in n:
                    r_path = n
            if not e_path:
                raise ValueError("Geen entities.json gevonden in .uniec3 bestand.")
            entities = json.loads(zf.read(e_path))
            relations = json.loads(zf.read(r_path)) if r_path else []
            try:
                self.building_id = int(e_path.split('buildings/')[1].split('/')[0])
            except Exception:
                self.building_id = 0

        for e in entities:
            eid = e['NTAEntityDataId']
            self.entities_by_id[eid] = e
            self.entities_by_type[e['NTAEntityId']].append(e)

        for rel in relations:
            pid = rel.get('ParentId', '')
            cid = rel.get('ChildId', '')
            pe = self.entities_by_id.get(pid)
            ce = self.entities_by_id.get(cid)
            if ce:
                self.children_of[pid].append(ce)
            if pe:
                self.parents_of[cid].append(pe)

    def children(self, entity_id: str, type_filter: str | None = None) -> list:
        cs = self.children_of.get(entity_id, [])
        if type_filter:
            cs = [c for c in cs if c['NTAEntityId'] == type_filter]
        return cs

    def parents(self, entity_id: str, type_filter: str | None = None) -> list:
        ps = self.parents_of.get(entity_id, [])
        if type_filter:
            ps = [p for p in ps if p['NTAEntityId'] == type_filter]
        return ps

    def first_child(self, entity_id: str, type_filter: str) -> dict | None:
        cs = self.children(entity_id, type_filter)
        return cs[0] if cs else None

    def first_parent(self, entity_id: str, type_filter: str) -> dict | None:
        ps = self.parents(entity_id, type_filter)
        return ps[0] if ps else None


# ─── CONSTRUCTIE-REGISTRY ─────────────────────────────────────────────────────

class ConstructieRegistry:
    def __init__(self):
        self._by_key: dict[str, str] = {}
        self._items: list[dict] = []

    def get_or_create(self, naam: str, constr_type: str,
                      rc: float | None = None,
                      u: float | None = None,
                      g: float | None = None) -> str:
        rc_s = str(round(rc, 3)) if rc else ''
        u_s = str(round(u, 3)) if u else ''
        g_s = str(round(g, 3)) if g else ''
        key = f"{naam}|{constr_type}|{rc_s}|{u_s}|{g_s}"
        if key in self._by_key:
            return self._by_key[key]
        g_new = _guid()
        self._by_key[key] = g_new
        self._items.append({'guid': g_new, 'naam': naam, 'type': constr_type,
                            'rc': rc, 'u': u, 'g': g})
        return g_new

    def items(self):
        return self._items


# ─── CONVERSIE-FUNCTIES ───────────────────────────────────────────────────────

def _detect_vent_type(data: Uniec3Data, vent: dict) -> str:
    """Bepaal VABI ventilatiesysteem-type (1-4) op basis van kind-entiteiten van VENT.

    In .uniec3 bestanden bestaat er geen VENT_SYS property; het systeem-type
    volgt uit welke sub-entiteiten aanwezig zijn:
      WARMTETERUG aanwezig          -> 4 (D, WTW)
      VENTDIS + VENTAAN (geen WTW)  -> 4 (D, gebalanceerd zonder WTW)
      VENTDIS, geen VENTAAN         -> 3 (C, mechanische afvoer)
      VENTAAN, geen VENTDIS         -> 2 (B, mechanische toevoer)
      anders                        -> 3 (C, standaard mechanisch)
    """
    child_types = {c['NTAEntityId'] for c in data.children(vent['NTAEntityDataId'])}
    if 'WARMTETERUG' in child_types:
        return '4'
    has_dis = 'VENTDIS' in child_types
    has_aan = 'VENTAAN' in child_types
    if has_dis and has_aan:
        return '4'   # gebalanceerd
    if has_dis:
        return '3'   # mechanische afvoer
    if has_aan:
        return '2'   # mechanische toevoer
    # Controleer ook op VENT_SYS property als fallback
    sys_val = _prop(vent, 'VENT_SYS')
    if sys_val:
        return VENT_SYS_MAP.get(sys_val, '3')
    return '3'


def _resolve_verw_type(opwek: dict) -> str:
    """Lees het verwarmingsopwekker-type uit een VERW-OPWEK entiteit.

    Probeert achtereenvolgens VERW-OPWEK_FABR (nieuw formaat) en
    VERW-OPWEK_TYPE (oud formaat). Valt terug op TYPE als FABR niet in de map staat.
    Geeft '16' (HR ketel) als ultiem default.
    """
    fabr = _prop(opwek, 'VERW-OPWEK_FABR')
    if fabr and fabr in VERW_FABR_MAP:
        return VERW_FABR_MAP[fabr]
    type_val = _prop(opwek, 'VERW-OPWEK_TYPE')
    if type_val and type_val in VERW_TYPE_MAP:
        return VERW_TYPE_MAP[type_val]
    return '16'


def _resolve_tapw_type(opwek: dict) -> str:
    """Lees het tapwateropwekker-type uit een TAPW-OPWEK entiteit.

    Probeert achtereenvolgens TAPW-OPWEK_FABR (nieuw formaat) en
    TAPW-OPWEK_TYPE (oud formaat). Valt terug op TYPE als FABR niet in de map staat.
    Geeft '1' (combi-ketel) als ultiem default.
    """
    fabr = _prop(opwek, 'TAPW-OPWEK_FABR')
    if fabr and fabr in TAPW_FABR_MAP:
        return TAPW_FABR_MAP[fabr]
    type_val = _prop(opwek, 'TAPW-OPWEK_TYPE')
    if type_val and type_val in TAPW_TYPE_MAP:
        return TAPW_TYPE_MAP[type_val]
    return '1'


def _build_installatie(data: Uniec3Data) -> dict:
    """Bouw het installatie-info dict vanuit alle INSTALLATIE-entiteiten.

    In .uniec3 bestanden zijn er doorgaans 3 aparte INSTALLATIE-entiteiten,
    elk met één kind-subsysteem: VENT, VERW of TAPW. We itereren alle
    INSTALLATIE-entiteiten en hun kinderen om de juiste subsystemen te vinden.
    """
    info = {
        'guid': _guid(),
        'vent_sys': '3',  # default: C (mechanisch)
        'verw_type': '1', # default: HR ketel (VABI 11.x TypeOpwekker=1)
        'tapw_type': '1', # default: combi-ketel (VABI 11.x TypeToestel=1)
    }
    installaties = data.entities_by_type.get('INSTALLATIE', [])
    if not installaties:
        return info

    vent_ent = verw_ent = tapw_ent = None

    # Doorloop ALLE INSTALLATIE-entiteiten en zoek de subsysteem-kinderen
    for inst in installaties:
        iid = inst['NTAEntityDataId']
        for child in data.children(iid):
            ctype = child['NTAEntityId']
            if ctype == 'VENT' and vent_ent is None:
                vent_ent = child
            elif ctype == 'VERW' and verw_ent is None:
                verw_ent = child
            elif ctype == 'TAPW' and tapw_ent is None:
                tapw_ent = child

    if vent_ent:
        info['vent_sys'] = _detect_vent_type(data, vent_ent)

    if verw_ent:
        opwek = data.first_child(verw_ent['NTAEntityDataId'], 'VERW-OPWEK')
        if opwek:
            info['verw_type'] = _resolve_verw_type(opwek)

    if tapw_ent:
        opwek = data.first_child(tapw_ent['NTAEntityDataId'], 'TAPW-OPWEK')
        if opwek:
            info['tapw_type'] = _resolve_tapw_type(opwek)

    return info


def _lookup_libconstrd(data: Uniec3Data, constrd: dict) -> dict | None:
    lib_id = _prop(constrd, 'CONSTRD_LIB')
    if lib_id and lib_id in data.entities_by_id:
        return data.entities_by_id[lib_id]
    return data.first_parent(constrd['NTAEntityDataId'], 'LIBCONSTRD')


def _lookup_libconstrt(data: Uniec3Data, constrt: dict) -> dict | None:
    lib_id = _prop(constrt, 'CONSTRT_LIB')
    if lib_id and lib_id in data.entities_by_id:
        return data.entities_by_id[lib_id]
    return data.first_parent(constrt['NTAEntityDataId'], 'LIBCONSTRT')


def _lookup_libconstrl(data: Uniec3Data, constrl: dict) -> dict | None:
    lib_id = _prop(constrl, 'CONSTRL_LIB')
    if lib_id and lib_id in data.entities_by_id:
        return data.entities_by_id[lib_id]
    return data.first_parent(constrl['NTAEntityDataId'], 'LIBCONSTRL')


def _process_begr(data: Uniec3Data, begr: dict, reg: ConstructieRegistry) -> dict:
    eid = begr['NTAEntityDataId']
    naam = _prop(begr, 'BEGR_OMSCHR') or 'Vlak'
    vlak = _prop(begr, 'BEGR_VLAK', 'VLAK_GEVEL')
    area = _num(begr, 'BEGR_A') or 0.0

    locatie = VLAK_TO_LOCATIE.get(vlak, '2')
    loc_override = _omschr_to_locatie(naam)
    if loc_override:
        locatie = loc_override

    begr_gevel = _prop(begr, 'BEGR_GEVEL')
    orientatie = _gevel_to_ori(begr_gevel)
    if orientatie is None:
        orientatie = _DEFAULT_ORI.get(locatie, '0')

    # Voor gevel-vlakken: leid locatie (voor/achter/links/rechts) af van compass-richting
    if vlak == 'VLAK_GEVEL' and not loc_override and begr_gevel:
        parts = begr_gevel.split('_')
        compass = parts[-1].upper() if parts else ''
        if compass in _GEVEL_COMPASS_TO_LOCATIE:
            locatie = _GEVEL_COMPASS_TO_LOCATIE[compass]

    hellingshoek = _HELLING.get(locatie, '6')

    constrd = data.first_child(eid, 'CONSTRD')
    rc = None
    constr_naam = naam
    constr_type = LIBCONSTRD_TO_TYPE.get(
        'LIBVLAK_GEVEL' if vlak == 'VLAK_GEVEL' else
        'LIBVLAK_DAK' if vlak == 'VLAK_DAK' else
        'LIBVLAK_VLOER' if vlak == 'VLAK_VLOER' else 'LIBVLAK_GEVEL', '0')

    if constrd:
        libcd = _lookup_libconstrd(data, constrd)
        if libcd:
            rc_raw = (_num(libcd, 'LIBCONSTRD_RC') or
                      _num(libcd, 'LIBCONSTRD_R') or
                      _num(libcd, 'LIBCONSTRD_RC_TOT'))
            if rc_raw:
                rc = rc_raw
            ltype = _prop(libcd, 'LIBCONSTRD_TYPE')
            if ltype in LIBCONSTRD_TO_TYPE:
                constr_type = LIBCONSTRD_TO_TYPE[ltype]
            constr_naam = _prop(libcd, 'LIBCONSTRD_OMSCHR') or naam

    constr_guid = reg.get_or_create(naam=constr_naam, constr_type=constr_type, rc=rc)

    deelvlakken = []
    total_transp_area = 0.0
    for constrt in data.children(eid, 'CONSTRT'):
        ct_area = _num(constrt, 'CONSTRT_A') or _num(constrt, 'CONSTRT_OPP') or 0.0
        total_transp_area += ct_area
        libcrt = _lookup_libconstrt(data, constrt)
        u_val = g_val = None
        dv_naam = 'Raam'
        dv_type = '2'
        if libcrt:
            u_val = (_num(libcrt, 'LIBCONSTRT_U') or
                     _num(libcrt, 'LIBCONSTRT_U_WAARDE') or
                     _num(libcrt, 'LIBCONSTRT_UWAARDE'))
            g_val = (_num(libcrt, 'LIBCONSTRT_G') or
                     _num(libcrt, 'LIBCONSTRT_GGL') or
                     _num(libcrt, 'LIBCONSTRT_G_WAARDE') or
                     _num(libcrt, 'LIBCONSTRT_GWAARDE'))
            lt = _prop(libcrt, 'LIBCONSTRT_TYPE')
            dv_type = LIBCONSTRT_TO_TYPE.get(lt, '2')
            dv_naam = _prop(libcrt, 'LIBCONSTRT_OMSCHR') or ('Raam' if dv_type == '2' else 'Deur')
        dv_guid = reg.get_or_create(naam=dv_naam, constr_type=dv_type, u=u_val, g=g_val)
        deelvlakken.append({
            'naam': dv_naam, 'guid': dv_guid, 'area': ct_area,
            'orientatie': orientatie, 'hellingshoek': hellingshoek,
            'u': u_val, 'g': g_val,
        })

    koudebruggen = []
    for constrl in data.children(eid, 'CONSTRL'):
        lengte = _num(constrl, 'CONSTRL_LEN') or _num(constrl, 'CONSTRL_LENG') or 0.0
        libcrl = _lookup_libconstrl(data, constrl)
        psi = None
        omschr = 'Koudebrug'
        if libcrl:
            psi = _num(libcrl, 'LIBCONSTRL_PSI')
            omschr = _prop(libcrl, 'LIBCONSTRL_OMSCHR') or omschr
        if lengte > 0 and psi is not None:
            koudebruggen.append({'omschr': omschr[:60], 'lengte': lengte, 'psi': psi})

    netto_area = max(0.0, area - total_transp_area)
    return {
        'naam': naam, 'locatie': locatie, 'orientatie': orientatie,
        'hellingshoek': hellingshoek,
        'area': area,             # bruto (BEGR_A)
        'netto_area': netto_area, # netto (bruto minus transparant)
        'constr_guid': constr_guid, 'constr_naam': constr_naam,
        'rc': rc, 'deelvlakken': deelvlakken, 'koudebruggen': koudebruggen,
    }


def _process_unit(data: Uniec3Data, unit: dict,
                  reg: ConstructieRegistry, inst_guid: str) -> dict | None:
    uid = unit['NTAEntityDataId']
    naam = _prop(unit, 'UNIT_OMSCHR') or f"Woning {uid[:8]}"

    straat = huisnr = postcode = woonplaats = ''
    afmobject = data.first_child(uid, 'AFMELDOBJECT')
    if afmobject:
        afloc = data.first_child(afmobject['NTAEntityDataId'], 'AFMELDLOCATIE')
        if afloc:
            straat = _prop(afloc, 'AFMELDLOCATIE_STRAAT')
            huisnr = _prop(afloc, 'AFMELDLOCATIE_HUISNR')
            postcode = _prop(afloc, 'AFMELDLOCATIE_PC')
            woonplaats = _prop(afloc, 'AFMELDLOCATIE_WOONPL')

    unit_rzs = data.children(uid, 'UNIT-RZ')
    if not unit_rzs:
        return None

    hoofdvlakken = []
    go_total = 0.0
    for urz in unit_rzs:
        go = _num(urz, 'UNIT-RZAG') or _num(urz, 'UNIT-RZ_AG') or _num(urz, 'UNIT_RZAG')
        if go:
            go_total += go
        for begr in data.children(urz['NTAEntityDataId'], 'BEGR'):
            hv = _process_begr(data, begr, reg)
            hoofdvlakken.append(hv)

    if not hoofdvlakken and not go_total:
        return None

    return {
        'naam': naam, 'straat': straat, 'huisnummer': huisnr,
        'postcode': postcode, 'woonplaats': woonplaats,
        'go': go_total or None, 'inst_guid': inst_guid,
        'hoofdvlakken': hoofdvlakken,
    }


# ─── XML BOUWERS ──────────────────────────────────────────────────────────────

def _xml_ventilatiesysteem(parent: Element, index: int, primary: bool):
    """Schrijft één Ventilatiesysteem in de VentilatiesysteemList."""
    vs = SubElement(parent, 'Ventilatiesysteem')
    vs.set('Index', str(index))
    _xml_text(vs, 'Guid', _guid())
    _xml_empty(vs, 'Merk')
    _xml_empty(vs, 'Type')
    _xml_text(vs, 'Installatiejaar', '0')
    _xml_text(vs, 'Subsysteem', '11' if primary else '-1')
    _xml_text(vs, 'Verblijfsgebied', '0.00')
    _xml_text(vs, 'KwaliteitsverklaringInvoermethode', '0')
    _xml_empty(vs, 'KwaliteitsverklaringMerk')
    _xml_empty(vs, 'KwaliteitsverklaringType')
    _xml_text(vs, 'KwaliteitsverklaringId', _ZERO_GUID)
    _xml_text(vs, 'IsSysteemVoorzienVanPassieveKoeling', '0')
    _xml_text(vs, 'ZwembadAanwezig', '0')
    _xml_text(vs, 'GebruiksoppervlakteZwembadruimte', '0.00')
    _xml_text(vs, 'Debietregeling', '-1')
    _xml_text(vs, 'IsDebietBekend', '0')
    _xml_text(vs, 'Debiet', '0')
    _xml_text(vs, 'Terugregeling', '-1')
    _xml_text(vs, 'Recirculatie', '-1')
    _xml_text(vs, 'RecirculatiePercentage', '0')
    _xml_text(vs, 'KwaliteitsverklaringVla', '0')
    _xml_text(vs, 'FCtrl', '0.00')
    _xml_empty(vs, 'CodeKvVla')
    _xml_text(vs, 'IsLbkAanwezig', '0')
    _xml_text(vs, 'IsLbkBinnenThermischeSchil', '0')
    _xml_text(vs, 'IsVerwarmingAangeslotenOpLbk', '0')
    _xml_text(vs, 'IsKoelingAangeslotenOpLbk', '0')
    _xml_text(vs, 'TypeWtw', '-1')
    _xml_text(vs, 'Volumeregeling', '-1')
    _xml_text(vs, 'Bypass', '-1')
    _xml_text(vs, 'BypassPercentage', '0')
    _xml_text(vs, 'BypassFabricagejaar', '-1')
    _xml_text(vs, 'KoudeterugwinningWtw', '0')
    _xml_text(vs, 'IsolatieKanaalBuitenaansluiting', '-1')
    _xml_text(vs, 'IsolatieDikteKanaalBuitenaansluiting', '0')
    _xml_text(vs, 'LambdaKanaalBuitenaansluiting', '0.000')
    _xml_text(vs, 'LengteKanaalBuitenaansluiting', '-1')
    _xml_text(vs, 'WerkelijkeLengteKanaalBuitenaansluiting', '0.00')
    _xml_text(vs, 'TypeVerklaringKvWtw', '-1')
    _xml_text(vs, 'RendementKvWtw', '0.000')
    _xml_text(vs, 'RendementKvWtwInclusiefDissipatie', '0')
    _xml_empty(vs, 'CodeKvWtw')
    _xml_text(vs, 'Luchtdichtheidsklasse', '0' if primary else '-1')
    _xml_text(vs, 'IsToevoerkanalenBuitenVerwarmdeZone', '0')
    _xml_text(vs, 'LengteKanalen', '-1')
    _xml_text(vs, 'IsolatiewaardeKanalen', '-1')
    _xml_text(vs, 'Ventilatoren', '3' if primary else '-1')
    _xml_text(vs, 'NominaalVermogen', '0')
    _xml_text(vs, 'ElektrischAsvermogen', '0')
    _xml_text(vs, 'TypeVentilator', '0' if primary else '-1')
    _xml_text(vs, 'Stroomsterkte', '0.00')
    _xml_text(vs, 'Spanning', '0.00')
    _xml_text(vs, 'Arbeidsfactor', '0.00')
    _xml_text(vs, 'FabricagejaarVentilatoren', '5' if primary else '-1')
    _xml_text(vs, 'TypeVerklaring', '-1')
    _xml_text(vs, 'ConstanteA', '0.0000000')
    _xml_text(vs, 'ConstanteB', '0.0000000')
    _xml_text(vs, 'ConstanteC', '0.0000000')
    _xml_empty(vs, 'CodeVentilatoren')
    _xml_text(vs, 'IsLintVerwarmingAanwezig', '0')
    _xml_text(vs, 'LintVerwarming', '-1')
    _xml_text(vs, 'AandeelDebietVoorverwarmd', '0')
    _xml_text(vs, 'MaximaalVermogen', '0')
    _xml_text(vs, 'MaximaleTemperatuurSprong', '0')
    _xml_text(vs, 'BuitenluchtTempVoorInschakelen', '0')
    _xml_text(vs, 'MaxInblaastempVoorRegeling', '0')


def _xml_koeling_opwekker(parent: Element, index: int):
    op = SubElement(parent, 'KoelingOpwekker')
    op.set('Index', str(index))
    _xml_text(op, 'Guid', _guid())
    _xml_empty(op, 'Merk')
    _xml_empty(op, 'Type')
    _xml_text(op, 'Installatiejaar', '0')
    _xml_text(op, 'TypeOpwekker', '-1')
    _xml_text(op, 'Expansie', '-1')
    _xml_text(op, 'Splitsysteem', '-1')
    _xml_text(op, 'Aandrijving', '-1')
    _xml_text(op, 'ElektrischVermogenGasmotor', '0.00')
    _xml_text(op, 'WkkVermogen', '0.00')
    _xml_text(op, 'Fabricagejaar', '-1')
    _xml_text(op, 'KoudeAfgifte', '-1')
    _xml_text(op, 'TypeCondensor', '-1')
    _xml_text(op, 'TypeLuchtgekoeldeCondensor', '-1')
    _xml_text(op, 'TypeWatergekoeldeCondensor', '-1')
    _xml_text(op, 'CircuitKoeltoren', '-1')
    _xml_text(op, 'VrijePassieveKoeling', '-1')
    _xml_text(op, 'TotaalVermogen', '0.00')
    _xml_text(op, 'AangeslotenOpWarmtepomp', '0')
    _xml_text(op, 'BodemtemperatuurBovenNulGraden', '0')
    _xml_text(op, 'WarmtepompRegeneratieTapwater', '0')
    _xml_text(op, 'Tapwatersysteem', '-1')
    _xml_text(op, 'KwaliteitsverklaringKoudeOpwekker', '0')
    _xml_text(op, 'Rendement', '0.000')
    _xml_text(op, 'PrimaireEnergiefactor', '0.0000')
    _xml_text(op, 'PrimaireEnergiefactorUitsluitendGemeten', '0')
    _xml_text(op, 'FactorHernieuwbaar', '0.0000')
    _xml_text(op, 'Co2Emissiecoefficient', '0.0000')
    _xml_empty(op, 'Code')


def _xml_verwarming_opwekker(parent: Element, index: int, verw_type: str = '-1'):
    """VABI 11.x VerwarmingOpwekker schema."""
    op = SubElement(parent, 'VerwarmingOpwekker')
    op.set('Index', str(index))
    _xml_text(op, 'Guid', _guid())
    _xml_empty(op, 'Merk')
    _xml_empty(op, 'Type')
    _xml_text(op, 'Installatiejaar', '0')
    _xml_text(op, 'TypeOpwekker', verw_type)
    _xml_text(op, 'SubType', '-1')
    _xml_text(op, 'AantalToestellenMetWaakvlam', '0')
    _xml_text(op, 'TypeWarmtepomp', '0' if verw_type == '9' else '-1')
    _xml_text(op, 'BronWarmtepomp', '0' if verw_type == '9' else '-1')
    _xml_text(op, 'BronGerealiseerdOfVergunning', '-1')
    _xml_text(op, 'TypeGrondwateraquifer', '-1')
    _xml_text(op, 'VoldoetAanMinCOP', '0')
    _xml_text(op, 'TypeBiomassakachel', '-1')
    _xml_text(op, 'BiomassaToestel', '-1')
    _xml_text(op, 'DirectGestookteLuchtverwarming', '0')
    _xml_text(op, 'HreLabelPresent', '0')
    _xml_text(op, 'HeeftStekker', '0')
    _xml_text(op, 'LokaleKachel', '-1')
    _xml_text(op, 'OpenVerbrandingstoestel', '0')
    _xml_text(op, 'NominaleBelasting', '0.0')
    _xml_text(op, 'StandbyAantal', '0')
    _xml_text(op, 'Opwekkingsvermogen', '0.0')
    _xml_text(op, 'ElektrischVermogenWkk', '0.0')
    _xml_text(op, 'IsAdditioneelGeplaatstBijRenovatie', '0')
    _xml_text(op, 'KwaliteitsverklaringWarmteopwekker', '0')
    _xml_text(op, 'KwaliteitsverklaringWarmteopwekkerId', _ZERO_GUID)
    _xml_text(op, 'KwaliteitsverklaringWarmtenet', '-1')
    _xml_text(op, 'TypeBron', '-1')
    _xml_empty(op, 'KwaliteitsverklaringMerk')
    _xml_text(op, 'KwaliteitsverklaringRendement', '0.000')
    _xml_text(op, 'KwaliteitsverklaringEnergiefractie', '0.000')
    _xml_text(op, 'KwaliteitsverklaringBronHTWarmtepomp', '-1')
    _xml_text(op, 'KwaliteitsverklaringPrimaireEnergiefactor', '0.0000')
    _xml_text(op, 'KwaliteitsverklaringPrimaireEnergiefactorUitsluitendGemeten', '0')
    _xml_text(op, 'KwaliteitsverklaringFactorHernieuwbaar', '0.0000')
    _xml_text(op, 'KwaliteitsverklaringCO2Emissiecoefficient', '0.0000')
    _xml_text(op, 'KwaliteitsverklaringWkkOmzettingsgetalWarmte', '0.000')
    _xml_text(op, 'KwaliteitsverklaringWkkOmzettingsgetalElektriciteit', '0.000')
    _xml_text(op, 'KwaliteitsverklaringDuurzaamBeng3', '0')
    _xml_text(op, 'KwaliteitsverklaringLuchtdebietToestel', '0.0')
    _xml_text(op, 'KwaliteitsverklaringModulerendeWarmtepomp', '0')
    _xml_empty(op, 'KwaliteitsverklaringCode')
    _xml_text(op, 'Hulpenergie', '0')
    _xml_text(op, 'HulpenergieTypeVerklaring', '0')
    _xml_text(op, 'HulpenergieInvoermethode', '0')
    _xml_text(op, 'HulpenergieId', _ZERO_GUID)
    _xml_empty(op, 'HulpenergieMerk')
    _xml_empty(op, 'HulpenergieType')
    _xml_text(op, 'HulpenergieConstanteA', '0.0000000000')
    _xml_text(op, 'HulpenergieConstanteB', '0.0000000000')
    _xml_text(op, 'HulpenergieConstanteC', '0.0000000000')
    _xml_text(op, 'HulpenergieBNominaal', '0.00')
    _xml_text(op, 'HulpenergieWaux', '0.00')
    _xml_empty(op, 'HulpenergieCode')
    _xml_text(op, 'HulpenergieFabricagejaarToestel', '-1')
    _xml_text(op, 'FabricagejaarToestelWkk', '-1')
    _xml_text(op, 'StandbyKwaliteitsverklaring', '0')
    _xml_text(op, 'StandbyElektriciteitsgebruik', '0.000')
    _xml_empty(op, 'StandbyCode')
    _xml_text(op, 'KwaliteitsverklaringInvoermethode', '0')


def _xml_tapwater_opwekker(parent: Element, index: int, tapw_type: str = '1'):
    op = SubElement(parent, 'TapwaterOpwekker')
    op.set('Index', str(index))
    _xml_text(op, 'Guid', _guid())
    _xml_empty(op, 'Merk')
    _xml_empty(op, 'Type')
    _xml_text(op, 'Jaar', '0')
    _xml_text(op, 'TypeToestel', tapw_type)
    _xml_text(op, 'TypeOpwekkerIndirectVerwarmdVat', '-1')
    _xml_text(op, 'OpwekkerIndirecteVerwarmdVatOokVoorRuimteverwarming', '0')
    _xml_text(op, 'Verwarmingsopwekker', '-1')
    _xml_text(op, 'Gaskeur', '-1')
    _xml_text(op, 'CwKlasse', '-1')
    _xml_text(op, 'BronWarmtepomp', '1' if tapw_type == '4' else '-1')
    _xml_text(op, 'FunctiesOpwekker', '-1')
    _xml_text(op, 'BronWarmtepompIndirectVerwarmdVat', '-1')
    _xml_text(op, 'Energiegebruik', '0.00')
    _xml_text(op, 'NominaalVermogenBekend', '-1')
    _xml_text(op, 'NominaalVermogen', '0.00')
    _xml_text(op, 'WarmtepompboilerInCollectiefSysteem', '0')
    _xml_text(op, 'BoosterwarmtepompGekoppeldAan', '-1')
    _xml_text(op, 'Sorptiewarmtepomp', '0')
    _xml_text(op, 'VermogenGasboiler', '-1')
    _xml_text(op, 'Opstelplaats', '-1')
    _xml_text(op, 'IsolatieVat', '-1')
    _xml_text(op, 'TypeBiomassa', '-1')
    _xml_text(op, 'VolumeBoilervatBekend', '0')
    _xml_text(op, 'VolumeBoilervat', '0')
    _xml_text(op, 'Installatiejaar', '-1')
    _xml_text(op, 'ElektrischVermogenWKK', '0.00')
    _xml_text(op, 'BouwjaarWKK', '-1')
    _xml_text(op, 'Hre', '0')
    _xml_text(op, 'OpenVerbrandingstoestel', '0')
    _xml_text(op, 'NominaleBelasting', '0.0')
    _xml_text(op, 'Kwaliteitsverklaring', '0')
    _xml_text(op, 'Aanvoertemperatuur', '0')
    _xml_text(op, 'KwaliteitsverklaringInvoermethode', '0')
    _xml_text(op, 'KwaliteitsverklaringId', _ZERO_GUID)
    _xml_empty(op, 'KwaliteitsverklaringMerk')
    _xml_text(op, 'PrimaireEnergiefactor', '0.0000')
    _xml_text(op, 'FactorHernieuwbaar', '0.0000')
    _xml_text(op, 'Co2Emissiecoefficient', '0.0000')
    _xml_text(op, 'CopBoosterwarmtepomp', '0.00')
    _xml_text(op, 'StandbyElektriciteitsvraagPls', '0.0000')
    _xml_text(op, 'PrimaireEnergiefactorUitsluitendGemeten', '0')
    _xml_text(op, 'Ventilatielucht', '0.0')
    _xml_text(op, 'TypeKwaliteitsverklaring', '0')
    _xml_empty(op, 'BcrgType')
    _xml_text(op, 'WarmtepompOpMenglucht', '0')
    _xml_text(op, 'OmzettingsgetalWarmte', '0.000')
    _xml_text(op, 'OmzettingsgetalElektrisch', '0.000')
    _xml_text(op, 'Rendement', '0.000')
    _xml_text(op, 'BrutoWarmtapwaterbehoefte', '0.00')
    _xml_text(op, 'DuurzaamBeng3', '0')
    _xml_text(op, 'QBS', '0.00')
    _xml_text(op, 'RendementInclusiefHulpenergie', '0')
    _xml_empty(op, 'Code')
    _xml_text(op, 'CwMixedairMi', '0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000')
    _xml_text(op, 'FwBuitenlucht', '0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00')


def _xml_leidingen(parent: Element, tag: str):
    """Schrijft een leidingen sub-element (VerwarmingLeidingen / TapwaterLeidingen)."""
    el = SubElement(parent, tag)
    el.set('Index', '-1')
    _xml_text(el, 'Guid', _guid())
    _xml_text(el, 'LeidinglengteDistributieleidingen', '-1')
    _xml_text(el, 'Leidinglengte', '0.00')
    _xml_text(el, 'MaximaleLeidinglengte', '0.00')
    _xml_text(el, 'LeidingenGeisoleerd', '-1')
    _xml_text(el, 'IsolatieJaar', '-1')
    _xml_text(el, 'OmgevingLeidingen', '-1')
    _xml_text(el, 'DiepteLeidingenVloerWandPlafond', '0')
    _xml_text(el, 'WarmtegeleidingMateriaalInbedding', '0.000')
    _xml_text(el, 'BinnendiameterLeidingZonderIsolatie', '0')
    _xml_text(el, 'DiameterLeidingMetIsolatie', '0')
    _xml_text(el, 'WarmtegeleidingIsolatiemateriaal', '0.000')
    _xml_text(el, 'BuitendiameterLeidingZonderIsolatie', '0')
    _xml_text(el, 'WarmtegeleidingLeidingmateriaal', '0.000')


def _xml_installatie(parent: Element, info: dict, index: int):
    """Schrijft een volledige Installatie conform VABI 11.x formaat."""
    inst = SubElement(parent, 'Installatie')
    inst.set('Index', str(index))
    _xml_text(inst, 'Guid', info['guid'])
    _xml_text(inst, 'Naam', 'Standaard installatie')
    _xml_empty(inst, 'Opmerkingen')
    _xml_text(inst, 'KoelingBron', '1')
    _xml_empty(inst, 'KoelingOpmerkingen')
    _xml_text(inst, 'VerwarmingBron', '1')
    _xml_empty(inst, 'VerwarmingOpmerkingen')

    # Ventilatie
    vent = SubElement(inst, 'Ventilatie')
    vent.set('Index', '-1')
    _xml_text(vent, 'Guid', _guid())
    _xml_text(vent, 'Systeem', '0')
    _xml_text(vent, 'CollectiefSysteemOokGebruiktVoorAndereRekenzones', '0')
    _xml_text(vent, 'GebruiksoppervlakteCollectief', '0.00')
    _xml_text(vent, 'AantalIdentiekeSystemen', '1')
    _xml_text(vent, 'AantalIdentiekeSystemenAuto', '1')
    _xml_text(vent, 'Ventilatiesysteem', info['vent_sys'])
    vs_list = _xml_list(vent, 'VentilatiesysteemList')
    _xml_ventilatiesysteem(vs_list, 0, primary=True)
    _xml_ventilatiesysteem(vs_list, 1, primary=False)
    _xml_text(vent, 'Bron', '1')
    _xml_empty(vent, 'Opmerkingen')

    # KoelingAfgifte
    ka = SubElement(inst, 'KoelingAfgifte')
    ka.set('Index', '-1')
    _xml_text(ka, 'Guid', _guid())
    _xml_text(ka, 'Afgiftesysteem', '-1')
    _xml_text(ka, 'AantalToestellen', '0')
    _xml_text(ka, 'VentilatorvermogenBekend', '0')
    _xml_text(ka, 'VermogenPerVentilator', '0.00')
    _xml_text(ka, 'AfgiftesysteemRegeling', '-1')

    # KoelingDistributie
    kd = SubElement(inst, 'KoelingDistributie')
    kd.set('Index', '-1')
    _xml_text(kd, 'Guid', _guid())
    _xml_text(kd, 'Distributiemedium', '-1')
    _xml_text(kd, 'Wateraanvoertemperatuur', '-1')
    _xml_text(kd, 'WaterzijdigInregelen', '0')
    _xml_text(kd, 'Ingeregeld', '-1')
    _xml_text(kd, 'Circulatiepomp', '-1')
    _xml_text(kd, 'CirculatiepompTotaalVermogen', '0')
    _xml_text(kd, 'CirculatiepompEnergieEfficientieIndex', '0.00')
    _xml_empty(kd, 'CirculatiePompCode')
    _xml_text(kd, 'TweedeCirculatiepompAanwezig', '0')
    _xml_text(kd, 'TweedeCirculatiepomp', '-1')
    _xml_text(kd, 'TweedeCirculatiepompVermogen', '0')
    _xml_text(kd, 'TweedeCirculatiepompEnergieEfficientieIndex', '0.00')
    _xml_empty(kd, 'TweedeCirculatiepompCode')
    _xml_text(kd, 'LeidingenDoorOngekoeldeRuimte', '0')
    _xml_text(kd, 'OngekoeldeRuimteLeidingenLengte', '-1')
    _xml_text(kd, 'OngekoeldeRuimteLeidinglengte', '0.00')
    _xml_text(kd, 'OngekoeldeRuimteMaximaleLeidinglengte', '0.00')
    _xml_text(kd, 'OngekoeldeRuimteLeidingenGeisoleerd', '-1')
    _xml_text(kd, 'OngekoeldeRuimteIsolatiejaar', '-1')
    _xml_text(kd, 'KleppenBeugelsGeisoleerd', '0')
    _xml_text(kd, 'OngekoeldeRuimteOmgevingLeidingen', '-1')
    _xml_text(kd, 'OngekoeldeRuimteDiepteLeidingenVloerWandPlafond', '0')
    _xml_text(kd, 'OngekoeldeRuimteWarmtegeleidingscoefficientMateriaalIngebed', '0.000')
    _xml_text(kd, 'OngekoeldeRuimteBinnendiameterLeidingZonderIsolatie', '0')
    _xml_text(kd, 'OngekoeldeRuimteBuitendiameterLeidingZonderIsolatie', '0')
    _xml_text(kd, 'OngekoeldeRuimteDiameterLeidingMetIsolatie', '0')
    _xml_text(kd, 'OngekoeldeRuimteWarmtegeleidingscoefficientIsolatiemateriaal', '0.000')
    _xml_text(kd, 'OngekoeldeRuimteWarmtegeleidingscoefficientLeidingmateriaal', '0.000')
    _xml_text(kd, 'AantalBouwlagenWaardoorLeidingenLopen', '0')
    _xml_text(kd, 'AantalWarmtemeters', '0')

    # KoelingOpwekking
    ko = SubElement(inst, 'KoelingOpwekking')
    ko.set('Index', '-1')
    _xml_text(ko, 'Guid', _guid())
    _xml_text(ko, 'KoelingAanwezig', '0')
    _xml_text(ko, 'Koelsysteem', '-1')
    _xml_text(ko, 'Oppervlak', '0.00')
    _xml_text(ko, 'AantalIdentiekeSystemen', '1')
    _xml_text(ko, 'AantalIdentiekeSystemenAuto', '1')
    _xml_text(ko, 'AantalOpwekkers', '-1')
    ko_list = _xml_list(ko, 'KoelingOpwekkers')
    for i in range(3):
        _xml_koeling_opwekker(ko_list, i)

    # Verwarming
    vw = SubElement(inst, 'Verwarming')
    vw.set('Index', '-1')
    _xml_text(vw, 'Guid', _guid())
    _xml_text(vw, 'Verwarmingsysteem', '2')
    _xml_text(vw, 'AgAangeslotenOpInstallatie', '0.00')
    _xml_text(vw, 'AantalIdentiekeSystemen', '1')
    _xml_text(vw, 'AantalIdentiekeSystemenAuto', '1')
    _xml_text(vw, 'AantalWarmteopwekkers', '0')
    vw_list = _xml_list(vw, 'VerwarmingOpwekkerList')
    for i in range(3):
        _xml_verwarming_opwekker(vw_list, i, verw_type=info['verw_type'] if i == 0 else '-1')

    # VerwarmingAfgifte
    va = SubElement(inst, 'VerwarmingAfgifte')
    va.set('Index', '-1')
    _xml_text(va, 'Guid', _guid())
    _xml_text(va, 'Afgiftesysteem', '2')
    _xml_text(va, 'TypeLuchtverwarming', '-1')
    _xml_text(va, 'AantalVentilatoren', '0')
    _xml_text(va, 'VentilatorvermogenBekend', '0')
    _xml_text(va, 'VermogenPerVentilator', '0.00')
    _xml_text(va, 'VentilatorenIndirecteLuchtverwarmer', '-1')
    _xml_text(va, 'Vertrekhoogte', '-1')
    _xml_text(va, 'TerugkeerWarmeLucht', '-1')
    _xml_text(va, 'TypeDirecteLuchtverwarmer', '-1')
    _xml_text(va, 'AantalRadialeVentilatoren', '0')
    _xml_text(va, 'RadialeVentilatorvermogenBekend', '0')
    _xml_text(va, 'VermogenPerRadialeVentilator', '0')
    _xml_text(va, 'Regeling', '2')

    # VerwarmingDistributie
    vd = SubElement(inst, 'VerwarmingDistributie')
    vd.set('Index', '-1')
    _xml_text(vd, 'Guid', _guid())
    _xml_text(vd, 'DistributieMedium', '0')
    _xml_text(vd, 'WaterAanvoertemperatuur', '3')
    _xml_text(vd, 'DistributieType', '0')
    _xml_text(vd, 'AantalAfgiftesystemen', '0')
    _xml_text(vd, 'WaterzijdigIngeregeld', '0')
    _xml_text(vd, 'SysteemIngeregeld', '-1')
    _xml_text(vd, 'Circulatiepomp', '3')
    _xml_text(vd, 'Vermogen', '0')
    _xml_text(vd, 'EnergieEfficientieIndexHoofdpomp', '0.00')
    _xml_empty(vd, 'Code')
    _xml_text(vd, 'TweedeCirculatiepompAanwezig', '0')
    _xml_text(vd, 'TweedeCirculatiepomp', '-1')
    _xml_text(vd, 'EnergieEfficientieIndexTweedeCirculatiepomp', '0.000')
    _xml_text(vd, 'TweedeCirculatiepompVermogen', '0')
    _xml_empty(vd, 'TweedeCirculatiepompCode')
    _xml_text(vd, 'VerwarmingssysteemTapwater', '0')
    _xml_leidingen(vd, 'VerwarmingLeidingen')
    _xml_text(vd, 'OnverwarmdLeidingenDoorRuimte', '0')
    _xml_leidingen(vd, 'LeidingenOnverwarmdeRuimte')
    _xml_text(vd, 'AppendagesBeugelsGeisoleerd', '0')
    _xml_text(vd, 'AantalBouwlagenWaardoorLeidingenLopen', '1')
    _xml_text(vd, 'AantalWarmtemeters', '0')
    _xml_text(vd, 'AantalBuffervaten', '-1')
    buf_list = _xml_list(vd, 'VerwarmingBuffervatList')
    for i in range(3):
        buf = SubElement(buf_list, 'VerwarmingBuffervat')
        buf.set('Index', str(i))
        _xml_text(buf, 'Guid', _guid())
        _xml_text(buf, 'Aantal', '0')
        _xml_text(buf, 'Kwaliteitsverklaring', '0')
        _xml_text(buf, 'InvoermethodeKwaliteitsverklaring', '0')
        _xml_text(buf, 'Volume', '0')
        _xml_text(buf, 'KwaliteitsverklaringId', _ZERO_GUID)
        _xml_empty(buf, 'KwaliteitsverklaringMerk')
        _xml_empty(buf, 'KwaliteitsverklaringType')
        _xml_text(buf, 'TypeKwaliteitsverklaring', '-1')
        _xml_text(buf, 'StandbyVerliesTestresultaten', '0.00')
        _xml_text(buf, 'WatertemperatuurBuffervatTestresultaten', '0.0')
        _xml_text(buf, 'OmgevingstemperatuurTestresultaten', '0.0')
        _xml_text(buf, 'WarmteoverdrachtscoefficientTestresultaten', '0.00')
        _xml_empty(buf, 'Code')
        _xml_text(buf, 'EnergielabelBuffervat', '-1')
        _xml_text(buf, 'FabricagejaarBuffervat', '-1')
        _xml_text(buf, 'BuffervatBinnenThermischeZone', '0')

    # Tapwater
    tw = SubElement(inst, 'Tapwater')
    tw.set('Index', '-1')
    _xml_text(tw, 'Guid', _guid())
    _xml_text(tw, 'AantalWarmtapwatersystemen', '0')
    tw_sys_list = _xml_list(tw, 'TapwatersysteemList')
    # Tapwatersysteem[0]
    tw_sys = SubElement(tw_sys_list, 'Tapwatersysteem')
    tw_sys.set('Index', '0')
    _xml_text(tw_sys, 'Guid', _guid())
    _xml_text(tw_sys, 'TypeInstallatie', '2')
    _xml_text(tw_sys, 'TotaalGebruiksoppervlakteSysteem', '0.00')
    _xml_text(tw_sys, 'AantalIdentiekeSystemen', '1')
    _xml_text(tw_sys, 'AantalIdentiekeSystemenAuto', '1')
    _xml_text(tw_sys, 'IsSportzaalInGebouw', '0')
    _xml_text(tw_sys, 'GebruiksoppervlakteSportzaal', '0.00')
    _xml_text(tw_sys, 'AangeslotenOp', '0')
    _xml_text(tw_sys, 'AantalBadkamers', '0')
    _xml_text(tw_sys, 'AantalKeukens', '0')
    _xml_text(tw_sys, 'AangeslotenGebruiksoppervlakte', '0.00')
    _xml_text(tw_sys, 'TypeOpwekker', '3')
    _xml_text(tw_sys, 'AantalOpwekkers', '0')
    tw_opw_list = _xml_list(tw_sys, 'TapwaterOpwekkerList')
    _xml_tapwater_opwekker(tw_opw_list, 0, tapw_type=info['tapw_type'])
    _xml_text(tw_sys, 'AantalVoorraadvaten', '-1')
    tw_vat_list = _xml_list(tw_sys, 'TapwaterVoorraadvatList')
    tw_vat = SubElement(tw_vat_list, 'TapwaterVoorraadvat')
    tw_vat.set('Index', '0')
    _xml_text(tw_vat, 'Guid', _guid())
    _xml_text(tw_vat, 'Aantal', '0')
    _xml_text(tw_vat, 'Volume', '0')
    _xml_text(tw_vat, 'Kwaliteitsverklaring', '0')
    _xml_text(tw_vat, 'KwaliteitsverklaringId', _ZERO_GUID)
    _xml_empty(tw_vat, 'KwaliteitsverklaringMerk')
    _xml_empty(tw_vat, 'KwaliteitsverklaringType')
    _xml_text(tw_vat, 'InvoermethodeKwaliteitsverklaring', '0')
    _xml_text(tw_vat, 'TypeKwaliteitsverklaring', '-1')
    _xml_text(tw_vat, 'StandbyVerliesTestresultaten', '0.00')
    _xml_text(tw_vat, 'WatertemperatuurBuffervatTestresultaten', '0.0')
    _xml_text(tw_vat, 'OmgevingstemperatuurTestresultaten', '0.0')
    _xml_text(tw_vat, 'WarmteoverdrachtscoefficientTestresultaten', '0.00')
    _xml_text(tw_vat, 'EnergielabelBuffervat', '-1')
    _xml_text(tw_vat, 'FabricagejaarBuffervat', '-1')
    _xml_text(tw_vat, 'VatBinnenThermischeZone', '0')
    _xml_empty(tw_vat, 'Code')
    _xml_text(tw_sys, 'DwtwAanwezig', '0')
    _xml_text(tw_sys, 'AantalDouches', '0')
    _xml_list(tw_sys, 'TapwaterDwtwList')
    _xml_text(tw_sys, 'LeidinglengteNaarKeuken', '1')
    _xml_text(tw_sys, 'LeidinglengteNaarBadkamer', '2')
    _xml_text(tw_sys, 'Leidinglengte', '-1')
    _xml_text(tw_sys, 'CirculatieleidingAanwezig', '0')
    _xml_text(tw_sys, 'AantalBouwlagenAangeslotenOpWarmtapwatersysteem', '0')
    _xml_text(tw_sys, 'AfleversetAanwezig', '1')
    _xml_text(tw_sys, 'IndividueleAfleversetPerObject', '1')
    _xml_text(tw_sys, 'AantalAfleversets', '0')
    _xml_text(tw_sys, 'IsolatieKleppenAppendagesBeugels', '-1')
    _xml_text(tw_sys, 'LengteCirculatieleiding', '-1')
    _xml_text(tw_sys, 'LengteCirculatieleidingValue', '0.00')
    _xml_text(tw_sys, 'MaximaleLengteCirculatieleidingValue', '0.00')
    _xml_leidingen(tw_sys, 'TapwaterLeidingen')
    _xml_text(tw_sys, 'LeidingenDoorOnverwarmdeRuimte', '0')
    _xml_text(tw_sys, 'IsolatieKleppenAppendagesBeugelsOnverwarmdeRuimte', '-1')
    _xml_text(tw_sys, 'LeidinglengteOnverwarmdeRuimte', '-1')
    _xml_text(tw_sys, 'LeidinglengteOnverwarmdeRuimteValue', '0.00')
    _xml_leidingen(tw_sys, 'TapwaterLeidingenOnverwarmdeRuimte')
    _xml_text(tw_sys, 'AantalAangeslotenWoonfuncties', '-1')
    _xml_text(tw_sys, 'BepaalAangeslotenWoonfunctiesUitAlgemeneGegevens', '1')
    _xml_text(tw_sys, 'VermogenPompCirculatieleiding', '-1')
    _xml_text(tw_sys, 'Vermogen', '0')
    _xml_empty(tw_sys, 'Code')
    _xml_text(tw_sys, 'Energieefficientieindex', '0.00')
    _xml_text(tw_sys, 'Pompregeling', '-1')
    _xml_text(tw, 'Bron', '1')
    _xml_empty(tw, 'Opmerkingen')

    # ZonneEnergieList
    _xml_list(inst, 'ZonneEnergieList')

    # VochtRegeling
    vr = SubElement(inst, 'VochtRegeling')
    vr.set('Index', '-1')
    _xml_text(vr, 'Guid', _guid())
    _xml_text(vr, 'BevochtigingAanwezig', '0')
    _xml_empty(vr, 'Merk')
    _xml_empty(vr, 'Type')
    _xml_text(vr, 'Installatiejaar', '0')
    _xml_text(vr, 'BevochtigingNietInLBK', '0')
    _xml_text(vr, 'BevochtigdGebruikersoppervlak', '0')
    _xml_text(vr, 'TypeBevochtigingsinstallatie', '-1')
    _xml_text(vr, 'Bron', '-1')
    _xml_empty(vr, 'Opmerkingen')


def _xml_constructie(parent: Element, c: dict, index: int):
    co = SubElement(parent, 'Constructie')
    co.set('Index', str(index))
    _xml_text(co, 'Guid', c['guid'])
    _xml_text(co, 'Hidden', '0')
    _xml_text(co, 'Blocked', '0')
    _xml_text(co, 'Naam', c['naam'])
    _xml_text(co, 'AutoNaam', '0')
    _xml_text(co, 'ConstructieType', c['type'])
    _xml_text(co, 'DeurMetRaamGlas65Procent', '0')
    _xml_text(co, 'RietenDak', '0')
    is_transp = c.get('type') in ('2', '3')  # raam of deur
    is_raam   = c.get('type') == '2'
    # Opaque: Invoer=4/Bron=2 → Rc zichtbaar in VABI lijst
    # Transparant: Invoer=2/Bron=1 → Uw zichtbaar via UGlas (ramen)
    _xml_text(co, 'Invoer', '2' if is_transp else '4')
    _xml_text(co, 'KwaliteitsverklaringInvoermethode', '0')
    _xml_text(co, 'GMinimaleEisenBbl', '0.00')
    _xml_text(co, 'OppervlaktePerConstructie', '0')
    _xml_text(co, 'Oppervlakte', '0.00')
    _xml_text(co, 'Arc', '0.00')
    _xml_empty(co, 'Merk')
    _xml_empty(co, 'Type')
    _xml_empty(co, 'Code')
    _xml_text(co, 'RcKwaliteitsverklaring', '0.00')
    _xml_text(co, 'UKwaliteitsverklaring', '0.00')
    _xml_text(co, 'GKwaliteitsverklaring', '0.00')
    _xml_text(co, 'KwaliteitsverklaringId', _ZERO_GUID)
    _xml_empty(co, 'KwaliteitsverklaringKozijn')
    _xml_empty(co, 'KwaliteitsverklaringGlas')
    _xml_empty(co, 'KwaliteitsverklaringAfstandhouder')
    _xml_empty(co, 'KwaliteitsverklaringType')
    _xml_empty(co, 'KwaliteitsverklaringIsolatieDikte')
    _xml_text(co, 'UKozijn', '0.00')
    # UGlas: voor ramen (type=2) de Uw-waarde invullen zodat VABI deze toont in de lijst
    u_str = _fmt(c.get('u'))
    _xml_text(co, 'UGlas', u_str if is_raam else '0.00')
    _xml_text(co, 'PsiGlas', '0.000')
    _xml_text(co, 'OmtrekBeglazing', '0.00')
    _xml_text(co, 'PsiGlasroede', '0.000')
    _xml_text(co, 'Glasroedelengte', '0.00')
    _xml_text(co, 'OppervlakteBeglazing', '0.00')
    _xml_text(co, 'OppervlakteKozijn', '0.00')
    _xml_text(co, 'RcInvoer', _fmt(c.get('rc')))
    _xml_text(co, 'UInvoer', _fmt(c.get('u')))
    _xml_text(co, 'GInvoer', _fmt(c.get('g')))
    _xml_text(co, 'IsolatieAanwezig', '-1')
    _xml_text(co, 'Rietdikte', '-1')
    _xml_text(co, 'IsolatiedikteOnbekend', '0')
    _xml_text(co, 'Isolatiedikte', '0')
    _xml_text(co, 'Bouwjaar', '-1')
    _xml_text(co, 'SpouwAanwezig', '0')
    _xml_text(co, 'Kozijn', '-1')
    _xml_text(co, 'Glas', '-1')
    _xml_text(co, 'ProductinformatieGWaarde', '0')
    _xml_text(co, 'Bron', '1' if is_transp else '2')
    _xml_empty(co, 'Opmerkingen')


def _xml_hoofdvlak(parent: Element, hv: dict, index: int):
    hvx = SubElement(parent, 'Hoofdvlak')
    hvx.set('Index', str(index))
    _xml_text(hvx, 'Guid', _guid())
    _xml_text(hvx, 'Naam', hv['naam'])
    _xml_text(hvx, 'AutoNaam', '0')
    _xml_text(hvx, 'Locatie', hv['locatie'])
    _xml_text(hvx, 'BouwdeelIsInactief', '0')
    _xml_text(hvx, 'Constructie', hv['constr_guid'])
    _xml_text(hvx, 'Oppervlakte', _fmt(hv['area']))
    _xml_text(hvx, 'BrutoOppervlakte', _fmt(hv['area']))
    _xml_text(hvx, 'NettoOppervlakte', _fmt(hv.get('netto_area', hv['area'])))
    _xml_text(hvx, 'Breedte', '0')
    _xml_text(hvx, 'HoogteOfLengte', '0')
    _xml_text(hvx, 'Orientatie', hv['orientatie'])
    _xml_text(hvx, 'Hellingshoek', hv['hellingshoek'])
    _xml_text(hvx, 'NaamConstructie', hv['constr_naam'])
    _xml_text(hvx, 'Rc', _fmt(hv.get('rc')))
    _xml_text(hvx, 'U', '0')
    _xml_text(hvx, 'G', '0')

    # DeelvlakList
    dv_list = _xml_list(hvx, 'DeelvlakList')
    for i, dv in enumerate(hv.get('deelvlakken', [])):
        dvx = SubElement(dv_list, 'Deelvlak')
        dvx.set('Index', str(i))
        _xml_text(dvx, 'Guid', _guid())
        _xml_text(dvx, 'Naam', dv['naam'])
        _xml_text(dvx, 'Constructie', dv['guid'])
        _xml_text(dvx, 'Oppervlakte', _fmt(dv['area']))
        _xml_text(dvx, 'Orientatie', dv['orientatie'])
        _xml_text(dvx, 'Hellingshoek', dv['hellingshoek'])
        _xml_text(dvx, 'U', _fmt(dv.get('u')))
        _xml_text(dvx, 'G', _fmt(dv.get('g')))
        _xml_text(dvx, 'NaamConstructie', dv['naam'])
        _xml_text(dvx, 'Belemmering', '0')
        _xml_text(dvx, 'Zonwering', '0')

    # KoudebrugList
    kb_list = _xml_list(hvx, 'KoudebrugList')
    for n, kb in enumerate(hv.get('koudebruggen', [])):
        kbx = SubElement(kb_list, 'Koudebrug')
        kbx.set('Index', str(n))
        _xml_text(kbx, 'Guid', _guid())
        _xml_text(kbx, 'Omschrijving', kb['omschr'])
        _xml_text(kbx, 'Lengte', _fmt(kb['lengte']))
        _xml_text(kbx, 'PsiWaarde', _fmt(kb['psi']))
        _xml_text(kbx, 'Toeslag25Procent', '0')


def _xml_rekenzone_algemeen(parent: Element, go: float | None):
    """Schrijft <Algemeen Index='-1'> in een Rekenzone conform VABI 11.x."""
    alg = SubElement(parent, 'Algemeen')
    alg.set('Index', '-1')
    _xml_text(alg, 'Guid', _guid())
    _xml_text(alg, 'Bouwjaar', '2025')
    _xml_text(alg, 'Renovatiejaar', '0')
    _xml_text(alg, 'Qv10Gemeten', '0')
    _xml_text(alg, 'Qv10Waarde', '0.000')
    _xml_text(alg, 'TypeBouwwijzeVloeren', '1')
    _xml_text(alg, 'TypeBouwwijzeWanden', '1')
    _xml_text(alg, 'KwaliteitsverklaringPCM', '0')
    _xml_text(alg, 'SoortelijkeWarmteList', '0')
    _xml_text(alg, 'MassaList', '0')
    _xml_empty(alg, 'KwaliteitsverklaringPCMCode')
    _xml_text(alg, 'TypePlafond', '-1')
    _xml_text(alg, 'Gebruiksoppervlakte', '0')  # altijd 0 op Rekenzone-niveau

    # Verdiepingen — 1 verdieping met de echte GO
    verd_list = SubElement(alg, 'Verdiepingen')
    verd_list.set('Index', '-1')
    _xml_text(verd_list, 'Guid', _ZERO_GUID)
    verd = SubElement(verd_list, 'Verdieping')
    verd.set('Index', '0')
    _xml_text(verd, 'Guid', _guid())
    _xml_text(verd, 'Gebruiksoppervlakte', _fmt(go) if go else '0.00')

    _xml_text(alg, 'Hoofdfunctie', '-1')

    # Deelfuncties — 4 lege items (VABI-standaard)
    df_list = SubElement(alg, 'Deelfuncties')
    df_list.set('Index', '-1')
    _xml_text(df_list, 'Guid', _ZERO_GUID)
    for i in range(4):
        df = SubElement(df_list, 'Deelfunctie')
        df.set('Index', str(i))
        _xml_text(df, 'Guid', _guid())
        _xml_text(df, 'Deelfunctie', '0')
        _xml_text(df, 'OppervlakteDeelfunctie', '0.00')
        _xml_text(df, 'Functie', '-1')

    _xml_text(alg, 'Leidingdoorvoeren', '1')
    _xml_text(alg, 'AantalLeidingdoorvoerenStandleidingen', '1')
    _xml_text(alg, 'AantalBouwlagenRekenzone', '1')
    _xml_text(alg, 'LeidingGeisoleerd', '0')
    _xml_text(alg, 'DoorLangsAndereAangrenzendeRekenzonesAvr', '0')
    _xml_text(alg, 'AantalDoorLangsAndereAangrenzendeRekenzonesAvr', '0')
    _xml_text(alg, 'AantalToiletgroepen', '0')
    _xml_text(alg, 'VentilatieveKoelingAanwezig', '0')
    _xml_text(alg, 'TypeVentilatieveKoeling', '-1')
    _xml_text(alg, 'BedieningVentilatieveKoeling', '-1')

    # Zomernachtventilaties — 1 lege item
    zn_list = SubElement(alg, 'Zomernachtventilaties')
    zn_list.set('Index', '-1')
    _xml_text(zn_list, 'Guid', _ZERO_GUID)
    zn = SubElement(zn_list, 'Zomernachtventilatie')
    zn.set('Index', '0')
    _xml_text(zn, 'Guid', _guid())
    _xml_empty(zn, 'Doorlaat')
    _xml_text(zn, 'CdCeFactoren', '0')
    _xml_text(zn, 'Oppervlakte', '0.00')
    _xml_text(zn, 'HoogteTotMaaiveld', '0.00')
    _xml_text(zn, 'HoogteOpening', '0.00')
    _xml_text(zn, 'Orientatie', '0')
    _xml_text(zn, 'Hoek', '0')
    _xml_text(zn, 'Cd', '0.00')
    _xml_text(zn, 'Ce', '0.00')

    _xml_text(alg, 'KwaliteitsverklaringVentilatieveKoeling', '0')
    _xml_text(alg, 'KwaliteitsverklaringRendementVentilatieveKoeling', '0.000')
    _xml_empty(alg, 'CodeKvVoorVentilatieveKoeling')
    _xml_text(alg, 'Bron', '1')
    _xml_empty(alg, 'Opmerkingen')


def _xml_object(parent: Element, woning: dict, index: int, gebouwhoogte: float = 0.0):
    obj = SubElement(parent, 'Object')
    obj.set('Index', str(index))
    _xml_text(obj, 'Guid', _guid())

    # ResultsMaatregelList — verplicht vóór Rekenzones
    _xml_list(obj, 'ResultsMaatregelList')

    # Rekenzones
    rzs = SubElement(obj, 'Rekenzones')
    rzs.set('Index', '-1')
    _xml_text(rzs, 'Guid', _ZERO_GUID)
    rz = SubElement(rzs, 'Rekenzone')
    rz.set('Index', '0')
    _xml_text(rz, 'Guid', _guid())

    # Rekenzone Algemeen (uitgebreid)
    _xml_rekenzone_algemeen(rz, woning.get('go'))

    # VerlichtingList — verplicht na Algemeen
    _xml_list(rz, 'VerlichtingList')

    # Geometrie
    geo = SubElement(rz, 'Geometrie')
    geo.set('Index', '-1')
    _xml_text(geo, 'Guid', _guid())
    for i, hv in enumerate(woning.get('hoofdvlakken', [])):
        _xml_hoofdvlak(geo, hv, i)

    # Maatwerk (lege lijst — wordt door VABI ingevuld)
    _xml_list(rz, 'Maatwerk')

    # Rekenzone metadata + installatie-koppeling (VABI 11.x)
    _xml_text(rz, 'ZoneType', '0')
    _xml_text(rz, 'Naam', 'Rekenzone')
    _xml_empty(rz, 'Installatie')
    _xml_empty(rz, 'Opmerkingen')
    _xml_text(rz, 'OwnInstallatieId', woning['inst_guid'])
    _xml_text(rz, 'InstallatieRefRekenzoneId', _ZERO_GUID)
    _xml_text(rz, 'KoelingOpwekkingRefRekenzoneId', _ZERO_GUID)
    _xml_text(rz, 'KoelingAfgifteRefRekenzoneId', _ZERO_GUID)
    _xml_text(rz, 'KoelingDistributieRefRekenzoneId', _ZERO_GUID)
    _xml_text(rz, 'VerwarmingRefRekenzoneId', _ZERO_GUID)
    _xml_text(rz, 'VerwarmingAfgifteRefRekenzoneId', _ZERO_GUID)
    _xml_text(rz, 'VerwarmingDistributieRefRekenzoneId', _ZERO_GUID)
    _xml_text(rz, 'VentilatieRefRekenzoneId', _ZERO_GUID)
    _xml_text(rz, 'TapwaterRefRekenzoneId', _ZERO_GUID)
    _xml_text(rz, 'VochtRegelingRefRekenzoneId', _ZERO_GUID)
    _xml_text(rz, 'KoelingBron', '-1')
    _xml_empty(rz, 'KoelingOpmerkingen')
    _xml_text(rz, 'VerwarmingBron', '-1')
    _xml_empty(rz, 'VerwarmingOpmerkingen')
    _xml_empty(rz, 'KoelingAfgifte')
    _xml_empty(rz, 'KoelingDistributie')
    _xml_empty(rz, 'KoelingOpwekking')
    _xml_empty(rz, 'Verwarming')
    _xml_empty(rz, 'VerwarmingDistributie')
    _xml_empty(rz, 'VerwarmingAfgifte')
    _xml_empty(rz, 'Tapwater')
    _xml_empty(rz, 'Ventilatie')
    _xml_empty(rz, 'VochtRegeling')
    _xml_empty(rz, 'ZonneEnergie')
    _xml_text(rz, 'AantalIdentiekeSystemenTapwaterSysteem1', '1')
    _xml_text(rz, 'AantalIdentiekeSystemenTapwaterSysteem2', '1')
    _xml_text(rz, 'AantalIdentiekeSystemenVerwarming', '1')
    _xml_text(rz, 'AantalIdentiekeSystemenVentilatie', '1')
    _xml_text(rz, 'AantalIdentiekeSystemenKoeling', '1')

    # ObjectAlgemeen
    obj_alg = SubElement(obj, 'ObjectAlgemeen')
    obj_alg.set('Index', '-1')
    _xml_text(obj_alg, 'Guid', _guid())

    # ObjectObject
    obj_obj = SubElement(obj_alg, 'ObjectObject')
    obj_obj.set('Index', '-1')
    _xml_text(obj_obj, 'Guid', _guid())
    _xml_text(obj_obj, 'NaamObject', woning['naam'])
    _xml_text(obj_obj, 'Objecttype', 'Woning')
    _xml_text(obj_obj, 'Bouwfase', 'Oplevering')
    _xml_text(obj_obj, 'Opname', 'Detailopname')
    _xml_text(obj_obj, 'UitgebreideMethodeKoudebruggen', '0')
    _xml_text(obj_obj, 'UitgebreideMethodeAorAos', '0')
    _xml_text(obj_obj, 'IsSubsidieAanwezigObv', '0')
    _xml_empty(obj_obj, 'SubsidieAanwezigObvText')
    _xml_text(obj_obj, 'IsWoningNomGebouwd', '0')

    # ObjectClassificatie
    oc = SubElement(obj_alg, 'ObjectClassificatie')
    oc.set('Index', '-1')
    _xml_text(oc, 'Guid', _guid())
    _xml_text(oc, 'Gebouwtype', '1')
    _xml_text(oc, 'Subtype', '1')
    _xml_text(oc, 'Ligging', '1')
    _xml_text(oc, 'Daktype', '-1')
    _xml_text(oc, 'AantalWoonfuncties', '0')
    _xml_text(oc, 'Gebouwhoogte', '0.00')

    # Adresgegevens
    adr = SubElement(obj_alg, 'Adresgegevens')
    adr.set('Index', '-1')
    _xml_text(adr, 'Guid', _guid())
    _xml_text(adr, 'Straat', woning.get('straat', ''))
    _xml_text(adr, 'Huisnummer', woning.get('huisnummer', ''))
    _xml_empty(adr, 'HuisletterHuisnummertoevoeging')
    _xml_empty(adr, 'Detailaanduiding')
    _xml_text(adr, 'Postcode', woning.get('postcode', ''))
    _xml_text(adr, 'Woonplaats', woning.get('woonplaats', ''))
    _xml_text(adr, 'AfwijkendeBagIdentificatie', '0')
    _xml_text(adr, 'BagIdentificatie', '-1')
    _xml_empty(adr, 'BagPandId')
    _xml_empty(adr, 'BagStandplaatsId')
    _xml_empty(adr, 'BagLigplaatsId')
    _xml_empty(adr, 'BagObjectId')
    _xml_empty(adr, 'Vhe')
    _xml_empty(adr, 'Complex')
    _xml_empty(adr, 'Buurt')
    _xml_empty(adr, 'Wijk')
    _xml_empty(adr, 'Gemeente')
    _xml_text(adr, 'OnzelfstandigeWoning', '0')
    _xml_empty(adr, 'Vestiging')
    _xml_empty(adr, 'TechnischComplex')
    _xml_empty(adr, 'FinancieelComplex')
    _xml_empty(adr, 'Foto')
    _xml_text(adr, 'IsObvReferentie', '0')
    _xml_empty(adr, 'RefStraat')
    _xml_text(adr, 'RefHuisnummer', '0')
    _xml_empty(adr, 'RefHuisletterHuisnummertoevoeging')
    _xml_empty(adr, 'RefDetailaanduiding')
    _xml_empty(adr, 'RefPostcode')
    _xml_empty(adr, 'RefPlaats')
    _xml_empty(adr, 'RefBagPandId')
    _xml_empty(adr, 'RefBagStandplaatsId')
    _xml_empty(adr, 'RefBagLigplaatsId')
    _xml_empty(adr, 'RefBagnummerObject')

    _xml_text(obj_alg, 'Registratiestatus', 'Object is niet geregistreerd')

    # RegistratiegegevensInvoer
    reg_inv = SubElement(obj_alg, 'RegistratiegegevensInvoer')
    reg_inv.set('Index', '-1')
    _xml_text(reg_inv, 'Guid', _guid())
    _xml_empty(reg_inv, 'Projectnaam')
    _xml_text(reg_inv, 'EpcVergunning', '0')
    _xml_empty(reg_inv, 'ProvisionalId')
    _xml_text(reg_inv, 'GtoBerekening', '0')
    _xml_text(reg_inv, 'GtoUren', '0')
    _xml_text(reg_inv, 'KoelsysteemKoellastBerekening', '0')
    _xml_text(reg_inv, 'Opnamedatum', '20250101')
    _xml_empty(reg_inv, 'OpnamedatumMaatwerkadvies')
    _xml_text(reg_inv, 'BezoekendeEpAdviseurGelijkAanRegistrerendeEpAdviseur', '1')
    _xml_empty(reg_inv, 'BezoekendeEpAdviseurVoorletters')
    _xml_empty(reg_inv, 'BezoekendeEpAdviseurTussenvoegsel')
    _xml_empty(reg_inv, 'BezoekendeEpAdviseurAchternaam')
    _xml_empty(reg_inv, 'Examennummer')
    _xml_empty(reg_inv, 'Invoerdatum')
    _xml_empty(reg_inv, 'InvoerendeEpAdviseur')
    _xml_empty(reg_inv, 'Certificaathouder')
    _xml_text(reg_inv, 'Gebruiker', '-1')
    _xml_text(reg_inv, 'Status', '2')
    _xml_text(reg_inv, 'RepresentatieveWoningen', '0')


# ─── HOOFDFUNCTIE ─────────────────────────────────────────────────────────────

def convert(uniec3_bytes: bytes, project_naam: str = '') -> bytes:
    """Converteert een .uniec3 bestand naar .epa bytes."""
    data = Uniec3Data(uniec3_bytes)
    reg = ConstructieRegistry()

    gebs = data.entities_by_type.get('GEB', [])
    if not project_naam:
        project_naam = _prop(gebs[0], 'GEB_OMSCHR') if gebs else 'VABI Project'
        if not project_naam:
            project_naam = 'VABI Project'

    gebouwhoogte = 0.0
    infils = data.entities_by_type.get('INFIL', [])
    if infils:
        gebouwhoogte = _num(infils[0], 'INFIL_BGH') or 0.0

    inst_info = _build_installatie(data)

    units = []
    if gebs:
        for geb in gebs:
            units.extend(data.children(geb['NTAEntityDataId'], 'UNIT'))
    if not units:
        units = data.entities_by_type.get('UNIT', [])
    if not units:
        unit_rzs = data.entities_by_type.get('UNIT-RZ', [])
        if unit_rzs:
            dummy = {
                'NTAEntityDataId': _guid(),
                'NTAEntityId': 'UNIT',
                'NTAPropertyDatas': [{'NTAPropertyId': 'UNIT_OMSCHR', 'Value': project_naam}],
            }
            for urz in unit_rzs:
                data.children_of[dummy['NTAEntityDataId']].append(urz)
            units = [dummy]

    woningen = []
    for unit in units:
        w = _process_unit(data, unit, reg, inst_info['guid'])
        if w:
            woningen.append(w)

    # ── XML opbouwen ─────────────────────────────────────────────────────────
    root = Element('Project')
    root.set('Index', '-1')

    # Versie-metadata
    _xml_text(root, 'Guid', _guid())
    _xml_text(root, 'Hidden', '0')
    _xml_text(root, 'CreatedBy', '1')
    _xml_text(root, 'ApplicationVersion', '11.2')
    _xml_text(root, 'XmlVersie', '110201001')
    _xml_text(root, 'FullApplicationVersion', '11.2.1')
    _xml_text(root, 'CalculationKernelVersion', '1.5')
    _xml_text(root, 'CalculationKernelVersionMwa', '2.1')
    _xml_text(root, 'LatestXmlUpgrade', '110200029')
    _xml_text(root, 'Backup', '0')
    _xml_text(root, 'FileName', '')
    _xml_text(root, 'IsEmpty', '0')
    _xml_text(root, 'IsVoorbeeldproject', '0')
    _xml_text(root, 'IsBasisVoorraadPrimairSysteem', '0')
    _xml_empty(root, 'OriginalVoorbeeldprojectFile')

    # Algemeen
    alg = SubElement(root, 'Algemeen')
    alg.set('Index', '-1')
    _xml_text(alg, 'Guid', _guid())
    pg = SubElement(alg, 'Projectgegevens')
    pg.set('Index', '-1')
    _xml_text(pg, 'Guid', _guid())
    _xml_text(pg, 'Objecttype', '0')
    _xml_text(pg, 'Bouwfase', '0')
    _xml_text(pg, 'Opname', '0')
    _xml_text(pg, 'UWaardeRaamMetOmtrekEnOppervlakte', '0')
    _xml_text(pg, 'Naam', project_naam)
    _xml_empty(pg, 'ProjectNr')
    adv = SubElement(alg, 'Adviseur')
    adv.set('Index', '-1')
    _xml_text(adv, 'Guid', _guid())
    opd = SubElement(alg, 'Opdrachtgever')
    opd.set('Index', '-1')
    _xml_text(opd, 'Guid', _guid())
    fin = SubElement(alg, 'Financieel')
    fin.set('Index', '-1')
    _xml_text(fin, 'Guid', _guid())

    # Installaties
    insts = SubElement(root, 'Installaties')
    insts.set('Index', '-1')
    _xml_text(insts, 'Guid', _ZERO_GUID)
    _xml_installatie(insts, inst_info, 0)

    # Constructies
    constrs = SubElement(root, 'Constructies')
    constrs.set('Index', '-1')
    _xml_text(constrs, 'Guid', _ZERO_GUID)
    for i, c in enumerate(reg.items()):
        _xml_constructie(constrs, c, i)

    # Maatregelen
    maatr = SubElement(root, 'Maatregelen')
    maatr.set('Index', '-1')
    _xml_text(maatr, 'Guid', _ZERO_GUID)

    # VrijeVeldenDefinities
    vvd = SubElement(root, 'VrijeVeldenDefinities')
    vvd.set('Index', '-1')
    _xml_text(vvd, 'Guid', _ZERO_GUID)

    # Registratiemomenten
    rm = SubElement(root, 'Registratiemomenten')
    rm.set('Index', '-1')
    _xml_text(rm, 'Guid', _ZERO_GUID)

    # Objecten
    objecten = SubElement(root, 'Objecten')
    objecten.set('Index', '-1')
    _xml_text(objecten, 'Guid', _ZERO_GUID)
    for i, w in enumerate(woningen):
        _xml_object(objecten, w, i, gebouwhoogte=gebouwhoogte)

    # Energieplannen
    ep = SubElement(root, 'Energieplannen')
    ep.set('Index', '-1')
    _xml_text(ep, 'Guid', _ZERO_GUID)

    # XML → bytes
    xml_bytes = _pretty(root)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo('project.xml')
        info.compress_type = zipfile.ZIP_DEFLATED
        info.create_system = 3   # Unix — zelfde als VABI zelf gebruikt
        zf.writestr(info, xml_bytes)
    buf.seek(0)
    return buf.read()


# ─── READER (parse-only, geen EPA XML) ────────────────────────────────────────

_ORI_CODE_TO_TEXT = {
    '0': 'Z', '1': 'ZW', '2': 'W', '3': 'NW',
    '4': 'N', '5': 'NO', '6': 'O', '7': 'ZO',
}

_LOCATIE_CODE_TO_NAAM = {
    '1': 'Dak', '2': 'Gevel', '3': 'Achtergevel',
    '4': 'Linkergevel', '5': 'Rechtergevel',
    '6': 'Vloer', '7': 'Vloer boven buiten',
}

_VENT_SYS_NAAM = {
    '1': 'Systeem A (natuurlijk)',
    '2': 'Systeem B (mechanisch)',
    '3': 'Systeem C (mech. afvoer)',
    '4': 'Systeem D / WTW',
}

_VERW_TYPE_NAAM = {
    '1': 'HR-ketel (gas)',
    '2': 'Niet-condenserend (gas)',
    '3': 'WKK',
    '9': 'Warmtepomp',
    '10': 'Stadsverwarming',
}

_TAPW_TYPE_NAAM = {
    '1': 'Combi-ketel (gas)',
    '3': 'Elektrische boiler',
    '4': 'Warmtepomp',
    '5': 'Stadsverwarming',
}


def _rc_gemiddelde(hoofdvlakken: list, locatie_codes: set) -> float | None:
    """Gewogen gemiddelde Rc voor de opgegeven locatie-codes (int)."""
    tot_rc_opp = tot_opp = 0.0
    for hv in hoofdvlakken:
        if hv['locatie_code'] not in locatie_codes:
            continue
        opp = hv.get('oppervlakte') or 0.0
        rc  = hv.get('rc')
        if rc and rc > 0 and opp > 0:
            tot_rc_opp += rc * opp
            tot_opp    += opp
    return round(tot_rc_opp / tot_opp, 2) if tot_opp > 0 else None


def _orientaties_uit_vlakken(hoofdvlakken: list) -> str:
    """Unieke oriëntaties van transparante deelvlakken (ramen), of als fallback gevelvlakken."""
    seen: set = set()
    result = []
    for hv in hoofdvlakken:
        for dv in hv.get('deelvlakken', []):
            ori = dv.get('ori', '')
            if ori and ori not in seen:
                seen.add(ori)
                result.append(ori)
    if not result:
        for hv in hoofdvlakken:
            if hv['locatie_code'] in {2, 3, 4, 5}:
                ori = hv.get('orientatie', '')
                if ori and ori not in seen:
                    seen.add(ori)
                    result.append(ori)
    return ', '.join(result)


def _nl_float(s: str):
    """Converteert een Nederlandse getal-string (komma als decimaalteken) naar float of None."""
    if not s:
        return None
    try:
        return float(str(s).replace(',', '.'))
    except (ValueError, TypeError):
        return None


def _adapt_hoofdvlak(hv_raw: dict) -> dict:
    """Converteert een _process_begr-resultaat naar het parser.py-compatibele formaat."""
    loc_str  = hv_raw['locatie']
    loc_code = int(loc_str) if loc_str.isdigit() else 0
    ori_tekst = _ORI_CODE_TO_TEXT.get(hv_raw.get('orientatie', ''), '')
    dvs = [
        {
            'naam': dv['naam'],
            'opp':  dv['area'],
            'u':    dv.get('u'),
            'g':    dv.get('g'),
            'ori':  _ORI_CODE_TO_TEXT.get(dv.get('orientatie', ''), ''),
        }
        for dv in hv_raw.get('deelvlakken', [])
    ]
    return {
        'naam':         hv_raw['naam'],
        'locatie':      _LOCATIE_CODE_TO_NAAM.get(loc_str, 'Onbekend'),
        'locatie_code': loc_code,
        'orientatie':   ori_tekst,
        'oppervlakte':  hv_raw['area'],
        'rc':           hv_raw.get('rc'),
        'u':            None,
        'g':            None,
        'deelvlakken':  dvs,
        'koudebruggen': hv_raw.get('koudebruggen', []),
    }


def parse_uniec3(uniec3_bytes: bytes, project_naam: str = '') -> dict:
    """Parseert een .uniec3 bestand en retourneert project + dwellings dicts.

    Teruggaveformaat is compatibel met parser.parse_epa() zodat dezelfde
    Flask-routes en templates hergebruikt worden.
    BENG-indicatoren worden direct uitgelezen uit de PRESTATIE-entiteiten.
    """
    from parser import label_color as _label_color, _resolve_label

    data = Uniec3Data(uniec3_bytes)
    reg  = ConstructieRegistry()

    gebs = data.entities_by_type.get('GEB', [])
    if not project_naam:
        project_naam = _prop(gebs[0], 'GEB_OMSCHR') if gebs else 'Uniec3 Project'
        if not project_naam:
            project_naam = 'Uniec3 Project'

    inst_info  = _build_installatie(data)
    vent_label = _VENT_SYS_NAAM.get(inst_info['vent_sys'],  f"Systeem {inst_info['vent_sys']}")
    verw_label = _VERW_TYPE_NAAM.get(inst_info['verw_type'], f"Type {inst_info['verw_type']}")
    tapw_label = _TAPW_TYPE_NAAM.get(inst_info['tapw_type'], f"Type {inst_info['tapw_type']}")

    # PRESTATIE-ids die tot een VARIANT behoren → die zijn de 'alternatieve' berekening.
    # We willen de basis-PRESTATIE (niet in VARIANT) per UNIT.
    variant_prest_ids: set = set()
    for var_ent in data.entities_by_type.get('VARIANT', []):
        for child in data.children_of.get(var_ent['NTAEntityDataId'], []):
            if child['NTAEntityId'] == 'PRESTATIE':
                variant_prest_ids.add(child['NTAEntityDataId'])

    # Zelfde UNIT-ophaal-logica als convert()
    units: list = []
    if gebs:
        for geb in gebs:
            units.extend(data.children(geb['NTAEntityDataId'], 'UNIT'))
    if not units:
        units = data.entities_by_type.get('UNIT', [])
    if not units:
        unit_rzs = data.entities_by_type.get('UNIT-RZ', [])
        if unit_rzs:
            dummy = {
                'NTAEntityDataId': _guid(),
                'NTAEntityId': 'UNIT',
                'NTAPropertyDatas': [{'NTAPropertyId': 'UNIT_OMSCHR', 'Value': project_naam}],
            }
            for urz in unit_rzs:
                data.children_of[dummy['NTAEntityDataId']].append(urz)
            units = [dummy]

    dwellings = []
    for i, unit in enumerate(units):
        raw = _process_unit(data, unit, reg, inst_info['guid'])
        if raw is None:
            continue
        hvs      = [_adapt_hoofdvlak(hv) for hv in raw.get('hoofdvlakken', [])]
        adres_r1 = ' '.join(filter(None, [raw.get('straat', ''), raw.get('huisnummer', '')]))
        adres_r2 = ' '.join(filter(None, [raw.get('postcode', ''), raw.get('woonplaats', '')]))
        adres    = ', '.join(filter(None, [adres_r1, adres_r2]))

        # BENG-waarden uit de basis-PRESTATIE (niet in VARIANT)
        unit_id   = unit['NTAEntityDataId']
        u_children = data.children_of.get(unit_id, [])
        prest_ref = next(
            (c for c in u_children if c['NTAEntityId'] == 'PRESTATIE'
             and c['NTAEntityDataId'] not in variant_prest_ids),
            None
        ) or next((c for c in u_children if c['NTAEntityId'] == 'PRESTATIE'), None)

        beng1 = beng2 = beng3 = to_juli = None
        energielabel = ''
        lc = '#9ca3af'
        if prest_ref:
            pr = data.entities_by_id.get(prest_ref['NTAEntityDataId'])
            if pr:
                pp = {p['NTAPropertyId']: p.get('Value', '')
                      for p in pr.get('NTAPropertyDatas', [])}
                beng1        = _nl_float(pp.get('EP_BENG1'))
                beng2        = _nl_float(pp.get('EP_BENG2'))
                beng3        = _nl_float(pp.get('EP_BENG3'))
                to_juli      = _nl_float(pp.get('EP_TOJULI'))
                energielabel = _resolve_label(pp.get('EP_ENERGIELABEL', ''))
                lc           = _label_color(energielabel)

        dwellings.append({
            'index':        i,
            'naam':         raw['naam'],
            'adres':        adres,
            'straat':       raw.get('straat', ''),
            'huisnummer':   raw.get('huisnummer', ''),
            'postcode':     raw.get('postcode', ''),
            'woonplaats':   raw.get('woonplaats', ''),
            'bouwfase':     '',
            'woning_type':  '',
            'datum_reg':    '',
            'beng1':        beng1,
            'beng2':        beng2,
            'beng3':        beng3,
            'to_juli':      to_juli,
            'energielabel': energielabel,
            'label_color':  lc,
            # Maatvoering & constructie
            'go':           raw.get('go'),
            'rc_dak':       _rc_gemiddelde(hvs, {1}),
            'rc_gevel':     _rc_gemiddelde(hvs, {2, 3, 4, 5}),
            'rc_vloer':     _rc_gemiddelde(hvs, {6, 7}),
            'orientaties':  _orientaties_uit_vlakken(hvs),
            'hoofdvlakken': hvs,
            # Installaties (extra — niet aanwezig bij EPA-bron)
            'vent_sys':     vent_label,
            'verw_type':    verw_label,
            'tapw_type':    tapw_label,
        })

    return {
        'project': {
            'naam':     project_naam,
            'nummer':   '',
            'datum':    '',
            'adviseur': '',
            'versie':   'Uniec3',
        },
        'dwellings': dwellings,
    }


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys, os
    if len(sys.argv) < 2:
        print("Gebruik: python uniec3_to_vabi.py <invoer.uniec3> [uitvoer.epa]")
        sys.exit(1)

    invoer = sys.argv[1]
    uitvoer = sys.argv[2] if len(sys.argv) > 2 else invoer.replace('.uniec3', '.epa')

    with open(invoer, 'rb') as f:
        data_bytes = f.read()

    naam = os.path.splitext(os.path.basename(invoer))[0]
    epa = convert(data_bytes, project_naam=naam)

    with open(uitvoer, 'wb') as f:
        f.write(epa)

    print(f"Geschreven: {uitvoer}  ({len(epa):,} bytes)")
