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
# Geldige codes afgeleid uit werkende Uniec3-referentiebestanden:
#   GF_BIJEENKIND, GF_BIJEENOVER, GF_GEZONDOVER, GF_KANT,
#   GF_LOGIES, GF_ONDERW, GF_SPORT, GF_WINKEL
HOOFDFUNCTIE_TO_GF = {
    '0':  'GF_BIJEENOVER',   # Overige gebruiksfunctie
    '2':  'GF_BIJEENOVER',   # Bijeenkomstfunctie  (GF_BIJEEN bestaat niet in Uniec3)
    '3':  'GF_BIJEENOVER',   # Celfunctie          (geen aparte code in Uniec3)
    '4':  'GF_GEZONDOVER',   # Gezondheidszorg bedgebonden
    '5':  'GF_GEZONDOVER',   # Gezondheidszorg niet-bedgebonden
    '6':  'GF_BIJEENOVER',   # Industriefunctie    (geen aparte code in Uniec3)
    '7':  'GF_KANT',         # Kantoorfunctie
    '8':  'GF_LOGIES',       # Logiesfunctie
    '9':  'GF_ONDERW',       # Onderwijsfunctie
    '10': 'GF_SPORT',        # Sportfunctie
    '11': 'GF_WINKEL',       # Winkelfunctie
    '12': 'GF_BIJEENOVER',   # Overige bijeenkomstfunctie
}

# VABI Verlichting Regeling → Uniec3 VERLZ_VERLREG
REGELING_TO_VERLREG = {
    '0': 'VERLZ_VERLREG_CA',
    '1': 'VERLZ_VERLREG_TW',
    '2': 'VERLZ_VERLREG_PA',
    '3': 'VERLZ_VERLREG_DL',
}


# ─── SCHEMA VERSIE-IDs (afkomstig uit werkende Uniec3 bestanden) ──────────────

ENTITY_VERSIONS = {
    'AFMELDINFO': 7104, 'AFMELDOBJECT': 7105, 'AFMELDLOCATIE': 7106,
    'BASIS': 7142, 'BEGR': 40, 'BEGR-FORM': 2124, 'BELEMMERING': 38,
    'CLIMATE': 7155, 'CONSTRD': 65, 'CONSTRERROR': 97, 'CONSTRKENMV': 71,
    'CONSTRKENMW': 70, 'CONSTRKRVENT': 87, 'CONSTRL': 67, 'CONSTRT': 66,
    'CONSTRWG': 69, 'CONSTRWWGVL': 88, 'CONSTRWWKLDR': 89, 'CONSTRZOMNAC': 90,
    'GEB': 5, 'GEB-EXTRA': 7163, 'GRUIMTE': 36, 'INFIL': 6, 'INFILUNIT': 42,
    'INSTALLATIE': 8, 'INSTALLATIONS-FORM': 4127,
    'KOEL': 25, 'KOEL-AFG': 85, 'KOEL-AFG-VENT': 86,
    'KOEL-DISTR': 81, 'KOEL-DISTR-BIN': 82, 'KOEL-DISTR-BUI': 83,
    'KOEL-DISTR-EIG': 84, 'KOEL-DISTR-POMP': 3125, 'KOEL-OPWEK': 80,
    'LIBCONSTRD': 60, 'LIBCONSTRFORM': 64, 'LIBCONSTRL': 62, 'LIBCONSTRT': 61,
    'LUCHTZOMNAC': 59, 'NGEBGEB-E': 7160,
    'PRESTATIE': 106, 'RZ': 13, 'RZFORM': 33, 'SETTINGS': 2109,
    'TAPW': 23, 'TAPW-AFG': 98, 'TAPW-DISTR': 99,
    'TAPW-DISTR-BIN': 100, 'TAPW-DISTR-BUI': 101, 'TAPW-DISTR-EIG': 102,
    'TAPW-DISTR-POMP': 3126, 'TAPW-OPWEK': 103, 'TAPW-UNIT-RZ': 111,
    'TAPW-VAT': 104,
    'UNIT': 29, 'UNIT-RZ': 30, 'UNIT-RZ-GF': 34,
    'VENT': 26, 'VENT-VERB': 1108, 'VENTAAN': 45, 'VENTCAP': 57,
    'VENTDEB': 56, 'VENTDIS': 58, 'VENTILATOR': 52, 'VENTILATOREIG': 53,
    'VENTZBR': 7122, 'VERL': 28, 'VERLZONE': 96,
    'VERW': 22, 'VERW-AFG': 78, 'VERW-AFG-VENT': 79,
    'VERW-DISTR': 74, 'VERW-DISTR-BIN': 75, 'VERW-DISTR-BUI': 76,
    'VERW-DISTR-EIG': 77, 'VERW-DISTR-POMP': 2125, 'VERW-OPWEK': 73,
    'VERW-VAT': 105,
    'VLEIDING': 43, 'VLEIDINGL': 44, 'VOORWARM': 48,
    'WARMTE-TOEV-KAN': 2108, 'WARMTETERUG': 46,
    # Resultaat-entiteiten (vereist door Uniec3 om te openen)
    'MWA-RESULTS': 7144, 'NTA-RESULTS': 7143,
    'RESULT-ENERGIEFUNCTIE': 2111, 'RESULT-ENERGIEGEBRUIK': 4128,
    'RESULT-GTO': 4130, 'RESULT-LSTRM': 7161, 'RESULT-TOJULI': 4129,
}

PROP_VERSIONS = {
    'AFMELDINFO': {'AFM_AANLEIDING': 17400, 'AFM_ADVISEUR': 17402,
                   'AFM_IDENTIFICATIEMETHODE': 17405, 'AFM_NAAM_ORIGINEEL': 17427,
                   'AFM_PROJECTNAAM': 17407, 'AFM_REGISTRATIE_ENERL': 17431,
                   'AFM_REPRESENTATIVITEIT': 17408, 'AFM_STATUS': 17409},
    'AFMELDOBJECT': {'AFMOBJ_ACTIE': 17415, 'AFMOBJ_CREDITS': 17414,
                     'AFMOBJ_ERRORS': 17420, 'AFMOBJ_REG_DATUM': 17413,
                     'AFMOBJ_REG_NUMMER': 17418, 'AFMOBJ_STATUS': 17412},
    'AFMELDLOCATIE': {'AFMLOC_BAG_ID': 17421, 'AFMLOC_HUISNR': 17423,
                      'AFMLOC_OMSCHR': 17411, 'AFMLOC_OPNAMEDATUM': 17410,
                      'AFMLOC_PLAATS': 17433, 'AFMLOC_POSTCODE': 17422,
                      'AFMLOC_REPRESENTATIEF': 17416, 'AFMLOC_STRAAT': 17432},
    'BASIS':    {'BASIS_DUMMY': 17666},
    'MWA-RESULTS': {'MWA-RESULTS_DUMMY': 17668},
    'NTA-RESULTS': {'NTA-RESULTS_DUMMY': 17667},
    'RESULT-ENERGIEFUNCTIE': {
        'RESULT-ENERGIEFUNCTIE_CAT': 17303, 'RESULT-ENERGIEFUNCTIE_CODE': 7236,
        'RESULT-ENERGIEFUNCTIE_EENHEID': 6238, 'RESULT-ENERGIEFUNCTIE_GROOTHEID': 6236,
        'RESULT-ENERGIEFUNCTIE_NAAM': 6235, 'RESULT-ENERGIEFUNCTIE_RESULTAAT': 6237,
        'RESULT-ENERGIEFUNCTIE_RES_ENER_NONPRIM': 17304,
        'RESULT-ENERGIEFUNCTIE_RES_ENER_PRIM': 17305,
        'RESULT-ENERGIEFUNCTIE_RES_HULPENER_NONPRIM': 17306,
        'RESULT-ENERGIEFUNCTIE_RES_HULPENER_PRIM': 17307,
    },
    'RESULT-ENERGIEGEBRUIK': {
        'RESULT-BOIM_GEBGEB': 17320, 'RESULT-CO2_CO2': 17324,
        'RESULT-ELEKTR_GEBGEB': 17314, 'RESULT-ELEKTR_NIETGEBGEB': 17315,
        'RESULT-ELEKTR_OPGEWEKT': 17316, 'RESULT-ELEKTR_TOT': 17328,
        'RESULT-EP_HERNIEUWBARE_ENERGIE_INDICATOR': 17358,
        'RESULT-EP_HERNIEUWBARE_ENERGIE_INDICATOR_EMG_FORF': 17359,
        'RESULT-EP_WARMTEBEHOEFTE': 17325,
        'RESULT-EWEK_EK': 17319, 'RESULT-EWEK_EW': 17318,
        'RESULT-GAS_GEBGEB': 17317, 'RESULT-HERNIEUW_ELEKTR': 17313,
        'RESULT-HERNIEUW_KOEL': 17312, 'RESULT-HERNIEUW_TAPW': 17311,
        'RESULT-HERNIEUW_TOT': 17327, 'RESULT-HERNIEUW_TOT_EMGFORF': 17861,
        'RESULT-HERNIEUW_VERW': 17310, 'RESULT-KARAKT_TOT': 17309,
        'RESULT-KOSTEN_ELEKTR': 17819, 'RESULT-NETTO_WARMTEVRAAG': 17365,
        'RESULT-OLIE_TOT': 17826, 'RESULT-OPP_GEBROPP': 17321,
        'RESULT-OPP_VERLOPP': 17322, 'RESULT-OPP_VORMFACTOR': 17323,
        'RESULT_KARAKT_OPGEW_E': 17308, 'RESULT_KARAKT_SOM_EPEH': 17326,
    },
    'RESULT-GTO': {
        'RESULT-GTO_FCTRL': 17339, 'RESULT-GTO_SPUIVENT_QVARGLIN': 17340,
        'RESULT-GTO_SPUIVENT_QVARGLOUT': 17341, 'RESULT-GTO_ZNVENT_QVARGLLIN': 17342,
        'RESULT-GTO_ZNVENT_QVARGLLOUT': 17343,
    },
    'RESULT-LSTRM': {
        'RESULT-LSTRM_LEAINZIMI': 17812, 'RESULT-LSTRM_SUPZIMI': 17814,
        'RESULT-LSTRM_VENTINZIMI': 17813,
    },
    'RESULT-TOJULI': {
        'RESULT-TOJULI_AANW_AANV_BER': 17798, 'RESULT-TOJULI_BEP_ZON': 17790,
        'RESULT-TOJULI_KOELCAP': 17791, 'RESULT-TOJULI_MAX': 17338,
        'RESULT-TOJULI_NOORD': 17330, 'RESULT-TOJULI_NOORD_OOST': 17331,
        'RESULT-TOJULI_NOORD_WEST': 17337, 'RESULT-TOJULI_OOST': 17332,
        'RESULT-TOJULI_RAAMFACTOR': 17818, 'RESULT-TOJULI_WEINIG_RAMEN': 17789,
        'RESULT-TOJULI_WEST': 17336, 'RESULT-TOJULI_ZUID': 17334,
        'RESULT-TOJULI_ZUID_OOST': 17333, 'RESULT-TOJULI_ZUID_WEST': 17335,
        'RESULT_TOJULI_RISICO': 17799, 'RESULT_TOJULI_TYPE_KOEL': 17807,
    },
    'BEGR':     {'BEGR_A': 775, 'BEGR_AOR': 776, 'BEGR_AOS': 777, 'BEGR_B': 778,
                 'BEGR_DAK': 779, 'BEGR_DUMMY': 5196, 'BEGR_GEVEL': 780,
                 'BEGR_HEL': 781, 'BEGR_KWAND': 783, 'BEGR_L': 784,
                 'BEGR_OMSCHR': 785, 'BEGR_OPM': 17460, 'BEGR_VLAK': 787,
                 'BEGR_VLOER': 788, 'BEGR_VLOER_BOVBUI': 17398, 'BEGR_VL_OMV': 786},
    'BEGR-FORM': {'BEGR-FORM_OPEN': 11251, 'BEGR-FORM_OPM': 17519},
    'BELEMMERING': {'BELEMM_CONST_BELEM': 17522, 'BELEMM_HOR_A_LINKS': 798,
                    'BELEMM_HOR_A_RECHTS': 800, 'BELEMM_HOR_B_LINKS': 801,
                    'BELEMM_HOR_B_RECHTS': 802, 'BELEMM_ZIJ_LINKS': 17525,
                    'BELEMM_ZIJ_RECHTS': 17526},
    'CLIMATE':  {'CLIMATE_HEAT_ISLAND': 17726, 'CLIMATE_KNMI_INV': 17723,
                 'CLIMATE_KNMI_STATION': 17725, 'CLIMATE_POSTCODE': 17724},
    'CONSTRD':  {'CONSTRD_B': 17462, 'CONSTRD_L': 17461, 'CONSTRD_LIB': 1059,
                 'CONSTRD_OPM': 7245, 'CONSTRD_OPP': 1060},
    'CONSTRERROR': {'CONSTRERROR_LINCONSTR': 17349, 'CONSTRERROR_OPEN': 11252,
                    'CONSTRERROR_OPM': 17520},
    'CONSTRKENMV': {'KENMV_OMTR_VL': 1078, 'KENMV_OPM': 17439},
    'CONSTRKENMW': {'KENMW_AFSTMV_VL': 1076, 'KENMW_OPM': 17438},
    'CONSTRKRVENT': {'KENMKR_OPM': 17457, 'KENMKR_VENT': 1079},
    'CONSTRL':  {'CONSTRL_LEN': 1071, 'CONSTRL_LIB': 1070, 'CONSTRL_OPM': 7248},
    'CONSTRT':  {'CONSTRT_AANT': 1062, 'CONSTRT_B': 17464, 'CONSTRT_BESCH': 1064,
                 'CONSTRT_GGL_ALT': 1066, 'CONSTRT_GGL_DIF': 1067,
                 'CONSTRT_L': 17463, 'CONSTRT_LIB': 1061, 'CONSTRT_OPM': 7246,
                 'CONSTRT_OPP': 1063, 'CONSTRT_REGEL': 1068,
                 'CONSTRT_ZNVENT': 1069, 'CONSTRT_ZONW': 1065},
    'CONSTRWG': {'CONSTRWG_B': 17466, 'CONSTRWG_L': 17465, 'CONSTRWG_LIB': 1074,
                 'CONSTRWG_OPM': 7247, 'CONSTRWG_OPP': 1075},
    'CONSTRWWGVL': {'KENMKR_WW_GVL': 1080, 'KENMKR_WW_GVL_OPM': 17458},
    'CONSTRWWKLDR': {'KENMKR_WW_KR': 1081, 'KENMKR_WW_KR_OPM': 17459},
    'CONSTRZOMNAC': {'CONSTRZOMNAC_BRDOORLV': 2064, 'CONSTRZOMNAC_DOORLF': 1238,
                     'CONSTRZOMNAC_DOORLV': 1239, 'CONSTRZOMNAC_HDOORL': 1237,
                     'CONSTRZOMNAC_HOPEN': 1236, 'CONSTRZOMNAC_OHOEKV': 1240},
    'GEB':      {'GEB_BWJR': 821, 'GEB_CALCNEEDED': 17357, 'GEB_DATE': 822,
                 'GEB_EIGEND': 823, 'GEB_HASMELD': 17350, 'GEB_OMSCHR': 824,
                 'GEB_OPEN': 11248, 'GEB_OPLVJR': 825, 'GEB_OPN': 827,
                 'GEB_PL': 828, 'GEB_RENOVJR': 829, 'GEB_SRTBW': 830,
                 'GEB_TYPEGEB': 831},
    'GEB-EXTRA': {'GEB-EXTRA_ADRS_GEB': 17824, 'GEB-EXTRA_OMSCHR_GEB': 17825},
    'GRUIMTE':  {'GRUIMTE_AG': 834, 'GRUIMTE_AV_INVOER': 17521,
                 'GRUIMTE_OMSCHR': 835, 'GRUIMTE_UNITID': 836},
    'INFIL':    {'INFIL_BGH': 952, 'INFIL_INVOER': 953, 'INFIL_OPEN': 11253,
                 'INFIL_VERV_METHODE': 17605},
    'INFILUNIT': {'INFILUNIT_BGH': 17435, 'INFILUNIT_QV': 954,
                  'INFILUNIT_QV_DEFAULT': 17291, 'INFILUNIT_QV_NON': 1171},
    'INSTALLATIE': {'INSTALL_AANTAL': 3192, 'INSTALL_NAAM': 17430,
                    'INSTALL_OMSCHR': 838, 'INSTALL_TYPE': 839},
    'INSTALLATIONS-FORM': {'INSTALLATIONS-FORM_DUMMY': 14286},
    'KOEL':     {'KOEL_OPEN': 3183, 'KOEL_OPM': 17443, 'KOEL_XXXX': 840},
    'KOEL-AFG': {'KOEL-AFG_TYPE_AFG': 1224, 'KOEL-AFG_TYPE_RUIM': 1226},
    'KOEL-AFG-VENT': {'KOEL-AFG-VENT_INV': 1232},
    'KOEL-DISTR': {'KOEL-DISTR_AAN_LAGEN': 1221, 'KOEL-DISTR_ONTW': 1212,
                   'KOEL-DISTR_POMP_INV': 1216, 'KOEL-DISTR_VERDAMP': 1211,
                   'KOEL-DISTR_WAT': 1213},
    'KOEL-DISTR-BUI': {'KOEL-DISTR-BUI_INV': 1206, 'KOEL-DISTR-BUI_ISO_LEI': 1209},
    'KOEL-DISTR-EIG': {'KOEL-DISTR-EIG_DEK': 1196, 'KOEL-DISTR-EIG_LAB_CON': 1197,
                       'KOEL-DISTR-EIG_LAB_ISO': 1198, 'KOEL-DISTR-EIG_RUIMTE': 1193},
    'KOEL-DISTR-POMP': {'KOEL-DISTR_POMP_OMSCHR': 12264},
    'KOEL-OPWEK': {'KOEL-OPWEK_FABR': 1177, 'KOEL-OPWEK_GEM': 1235,
                   'KOEL-OPWEK_INVOER': 1173, 'KOEL-OPWEK_TYPE': 1172},
    'LUCHTZOMNAC': {'LUCHTZOMNAC_BED': 1039},
    'NGEBGEB-E': {'NGEBGEB-E_INVOER': 17802, 'NGEBGEB-E_MAX': 17806,
                  'NGEBGEB-E_METHODE': 17801, 'NGEBGEB-E_MIN': 17805,
                  'NGEBGEB-E_OPEN': 17808, 'NGEBGEB-E_PER_M2': 17804,
                  'NGEBGEB-E_VAST': 17803},
    'LIBCONSTRD': {'LIBCONSTRD_BEPALING': 12282, 'LIBCONSTRD_DIKTE_ISO': 12283,
                   'LIBCONSTRD_DIKTE_RIET': 12284, 'LIBCONSTRD_METH': 1041,
                   'LIBCONSTRD_OMSCHR': 1042, 'LIBCONSTRD_RC': 1043,
                   'LIBCONSTRD_TYPE': 1044},
    'LIBCONSTRFORM': {'LIBCONSTRFORM_KOZ': 1045, 'LIBCONSTRFORM_OPEN': 11262},
    'LIBCONSTRL': {'LIBCONSTRL_BEPALING': 17290, 'LIBCONSTRL_METH': 1046,
                   'LIBCONSTRL_OMSCHR': 1047, 'LIBCONSTRL_POS': 1048,
                   'LIBCONSTRL_PSI': 1049},
    'LIBCONSTRT': {'LIBCONSTRT_AC': 1050, 'LIBCONSTRT_BEPALING': 13284,
                   'LIBCONSTRT_G': 1051, 'LIBCONSTRT_KOZ': 13283,
                   'LIBCONSTRT_METH': 1052, 'LIBCONSTRT_OMSCHR': 1053,
                   'LIBCONSTRT_TYPE': 1054, 'LIBCONSTRT_U': 1055},
    'PRESTATIE': {'EP_BENG1': 3175, 'EP_BENG2': 3176, 'EP_BENG3': 3177,
                  'EP_ENERGIELABEL': 15287},
    'RZ':       {'RZ_BOUWLG': 867, 'RZ_BOUWW_VL': 17527, 'RZ_BOUWW_W': 17528,
                 'RZ_CM': 869, 'RZ_OMSCHR': 870, 'RZ_TYPEPLFND': 871,
                 'RZ_TYPEZ': 872},
    'RZFORM':   {'RZFORM_CALCUNIT': 874, 'RZFORM_OPEN': 11250},
    'SETTINGS': {'SETTINGS_MAATADVIES': 17557, 'SETTINGS_THBRUG': 5197,
                 'SETTINGS_VARIANTEN': 17556},
    'TAPW':     {'TAPW_OPEN': 11255, 'TAPW_OPM': 17442, 'TAPW_XXXX': 930},
    'TAPW-AFG': {'TAPW-AFG_LEI_AANR': 3158},
    'TAPW-DISTR': {'TAPW-DISTR_CIRC': 3121, 'TAPW-DISTR_ZONE': 3122},
    'TAPW-DISTR-BIN': {'TAPW-DISTR-BIN_INV': 3137},
    'TAPW-DISTR-BUI': {'TAPW-DISTR-BUI_INV': 3144},
    'TAPW-DISTR-EIG': {'TAPW-DISTR-EIG_DEK': 3153, 'TAPW-DISTR-EIG_LAB_CON': 3154,
                       'TAPW-DISTR-EIG_LAB_ISO': 3155, 'TAPW-DISTR-EIG_RUIMTE': 3150},
    'TAPW-DISTR-POMP': {'TAPW-DISTR_POMP_OMSCHR': 12269},
    'TAPW-OPWEK': {'TAPW-OPWEK_GEM': 3068, 'TAPW-OPWEK_INV': 3066,
                   'TAPW-OPWEK_TYPE': 3064},
    'TAPW-UNIT-RZ': {'TAPW-UNIT-RZ_OPP': 3190, 'TAPW-UNIT-RZ_OPPMAX': 7244},
    'TAPW-VAT': {'TAPW-VAT_AANT': 3119, 'TAPW-VAT_INV': 3108},
    'UNIT':     {'UNIT_AANTA': 935, 'UNIT_AANTU': 936, 'UNIT_OMSCHR': 937,
                 'UNIT_TYPEGEB': 939},
    'UNIT-RZ':  {'UNIT-RZBLAAG': 943, 'UNIT-RZCM': 17434, 'UNIT-RZID': 946},
    'UNIT-RZ-GF': {'UNIT-RZ-GFAG': 944, 'UNIT-RZ-GFID': 945},
    'VENT':     {'VENT_FCTRL': 969, 'VENT_GEM': 5187, 'VENT_INVOER': 966,
                 'VENT_LBK': 17351, 'VENT_OPEN': 11256, 'VENT_OPM': 17444,
                 'VENT_OPP_GEM': 17352, 'VENT_OPP_LBK': 17353,
                 'VENT_PKOEL': 17393, 'VENT_SYS': 964, 'VENT_SYSVAR': 967,
                 'VENT_VARIANT': 1241, 'VENT_VERB': 965, 'VENT_VERBL': 4193},
    'VENT-VERB': {'VENT-VERB_OMSCHR': 4187},
    'VENTAAN':  {'VENTAAN_FCTRL': 975, 'VENTAAN_INVOER': 972, 'VENTAAN_SYS': 970,
                 'VENTAAN_SYSVAR': 973, 'VENTAAN_VARIANT': 1242,
                 'VENTAAN_VERB': 971, 'VENTAAN_VERBL': 4194},
    'VENTCAP':  {'VENTCAP_MD': 1025, 'VENTCAP_MV': 1026, 'VENTCAP_NAOS': 1024,
                 'VENTCAP_ND': 1022, 'VENTCAP_NV': 1023},
    'VENTDEB':  {'VENTDEB_CAP': 1020, 'VENTDEB_CAPTAB': 1021,
                 'VENTDEB_ZBR': 17531, 'VENTDEB_ZBRTAB': 17534},
    'VENTDIS':  {'VENTDIS_C': 1030, 'VENTDIS_CKOEL': 1032, 'VENTDIS_CVERW': 1031,
                 'VENTDIS_DEB': 17534, 'VENTDIS_DICHT': 1029, 'VENTDIS_LBK': 1033,
                 'VENTDIS_REC': 17535},
    'VENTILATOR': {},
    'VENTILATOREIG': {},
    'VENTZBR':  {'VENTZBR_AANW': 17532, 'VENTZBR_AG': 17533},
    'VERL':     {'VERL_DAGLREG': 2019, 'VERL_OPEN': 11257,
                 'VERL_PARVERM_INV': 2018, 'VERL_VERM_INV': 2017},
    'VERLZONE': {'VERLZ_A': 2024, 'VERLZ_DAGLREG': 17535, 'VERLZ_FD': 2033,
                 'VERLZ_FD_NON': 17536, 'VERLZ_F_AFZ': 6234, 'VERLZ_KAG30': 17396,
                 'VERLZ_OMSCHR': 2023, 'VERLZ_PN': 2025, 'VERLZ_VERLREG': 17395,
                 'VERLZ_WL': 17347},
    'VERW':     {'VERW_OPEN': 11254, 'VERW_OPM': 17441, 'VERW_XXXX': 949},
    'VERW-AFG': {'VERW-AFG_TYPE_AFG': 1150, 'VERW-AFG_TYPE_RUIM': 1158,
                 'VERW-AFG_VERT': 1151},
    'VERW-AFG-VENT': {'VERW-AFG-VENT_INV': 1166, 'VERW-AFG-VENT_SRT': 1167},
    'VERW-DISTR': {'VERW-DISTR_AANV_POMP': 11263, 'VERW-DISTR_AAN_LAGEN': 1146,
                   'VERW-DISTR_ONTW': 1136, 'VERW-DISTR_POMP_INV': 1141,
                   'VERW-DISTR_TYPE': 1135, 'VERW-DISTR_WAT': 1138},
    'VERW-DISTR-BIN': {'VERW-DISTR-BIN_INV': 1124, 'VERW-DISTR-BIN_ISO_KLE': 1128,
                       'VERW-DISTR-BIN_ISO_LEI': 1127, 'VERW-DISTR-BIN_LEN': 1126},
    'VERW-DISTR-BUI': {'VERW-DISTR-BUI_INV': 1130, 'VERW-DISTR-BUI_ISO_KLE': 1134,
                       'VERW-DISTR-BUI_ISO_LEI': 1133, 'VERW-DISTR-BUI_LEN': 1132},
    'VERW-DISTR-EIG': {'VERW-DISTR-EIG_DEK': 1120, 'VERW-DISTR-EIG_LAB_CON': 1121,
                       'VERW-DISTR-EIG_LAB_ISO': 1122, 'VERW-DISTR-EIG_RUIMTE': 1117},
    'VERW-DISTR-POMP': {'VERW-DISTR_POMP_OMSCHR': 11266},
    'VERW-OPWEK': {'VERW-OPWEK_FABR': 1096, 'VERW-OPWEK_FUNCTIE': 1170,
                   'VERW-OPWEK_GEM': 1244, 'VERW-OPWEK_INVOER': 1083,
                   'VERW-OPWEK_POMP': 1088, 'VERW-OPWEK_TOE_AAN': 1104,
                   'VERW-OPWEK_TYPE': 1082},
    'VERW-VAT': {'VERW-VAT_AANT': 3119},
    'VLEIDING': {'VLEIDING_INVOER': 956, 'VLEIDING_TOI': 1040},
    'VLEIDINGL': {'VLEIDINGL_AAN': 957, 'VLEIDINGL_ARZ': 960, 'VLEIDINGL_ISO': 958},
    'VOORWARM': {'VOORWARM_AAN': 988},
    'WARMTE-TOEV-KAN': {},
    'WARMTETERUG': {},
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
    """Maak een Uniec3 entity dict met correcte schema versie-IDs."""
    entity_ver  = ENTITY_VERSIONS.get(etype, 100)
    prop_ver_map = PROP_VERSIONS.get(etype, {})
    ts = _now()
    prop_list = []
    for prop_id, value in props.items():
        pver = prop_ver_map.get(prop_id, 10000)
        entry = {
            'NTAPropertyId': prop_id,
            'NTAPropertyVersionId': pver,
            'NTAPropertyDataId': f'{eid}:{prop_id}',
            'Status': 2,
            'Timestamp': ts,
        }
        if value != '':
            entry['Value'] = str(value)
        prop_list.append(entry)
    return {
        'NTAEntityId': etype,
        'NTAEntityVersionId': entity_ver,
        'Order': order,
        'BuildingId': _BUILD_ID,
        'NTAEntityDataId': eid,
        'Status': 2,
        'Timestamp': ts,
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
                'u':    _f(c, 'Uwaardeglasconstructie') or _f(c, 'U'),
                'g':    _f(c, 'Gwaarde') or _f(c, 'G'),
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

            # Deelgebruiksfuncties (meerdere gebruiksfuncties binnen één rekenzone)
            deelfuncties = []
            if alg is not None:
                deelfuncties_el = alg.find('Deelfuncties')
                if deelfuncties_el is not None:
                    for df in deelfuncties_el.findall('Deelfunctie'):
                        functie = _txt(df, 'Functie') or '-1'
                        opp = _f(df, 'OppervlakteDeelfunctie')
                        if functie not in ('-1', '') and opp > 0.0:
                            deelfuncties.append({
                                'functie': functie,
                                'opp':     opp,
                            })

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
                'deelfuncties':  deelfuncties,
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

    # ── AFMELDINFO (standalone, vereist door Uniec3) ───────────────────────────
    afmeldinfo_id = _guid()
    _add(entities, relations, afmeldinfo_id, 'AFMELDINFO', {
        'AFM_AANLEIDING':        'AFM_AANL_AANVR',
        'AFM_ADVISEUR':          'AFM_ADVISEUR_ZELFDE',
        'AFM_IDENTIFICATIEMETHODE': 'IDENTM_ZONDER_BAG',
        'AFM_NAAM_ORIGINEEL':    gebouw_naam,
        'AFM_PROJECTNAAM':       '',
        'AFM_REGISTRATIE_ENERL': 'AFM_LBL_GEB_GEHEEL',
        'AFM_REPRESENTATIVITEIT': 'AFM_REPRES_NIET',
        'AFM_STATUS':            '',
    })

    # ── AFMELDOBJECT → AFMELDLOCATIE (kind van GEB) ───────────────────────────
    afmobj_id = _guid()
    _add(entities, relations, afmobj_id, 'AFMELDOBJECT', {
        'AFMOBJ_ACTIE':      'AFM_ACTIE_NIEUW',
        'AFMOBJ_CREDITS':    '',
        'AFMOBJ_ERRORS':     '',
        'AFMOBJ_REG_DATUM':  '',
        'AFMOBJ_REG_NUMMER': '',
        'AFMOBJ_STATUS':     '0',
    })
    _link(relations, geb_id, 'GEB', afmobj_id, 'AFMELDOBJECT')

    afmloc_id = _guid()
    _add(entities, relations, afmloc_id, 'AFMELDLOCATIE', {
        'AFMLOC_BAG_ID':        '',
        'AFMLOC_HUISNR':        '',
        'AFMLOC_OMSCHR':        gebouw_naam,
        'AFMLOC_OPNAMEDATUM':   '',
        'AFMLOC_PLAATS':        '',
        'AFMLOC_POSTCODE':      '',
        'AFMLOC_REPRESENTATIEF': 'false',
        'AFMLOC_STRAAT':        '',
    })
    _link(relations, afmobj_id, 'AFMELDOBJECT', afmloc_id, 'AFMELDLOCATIE')

    # ── LUCHTZOMNAC (standalone, vereist voor utiliteitsgebouwen) ─────────────
    luchtzomnac_id = _guid()
    _add(entities, relations, luchtzomnac_id, 'LUCHTZOMNAC', {
        'LUCHTZOMNAC_BED': '',
    })

    # ── NGEBGEB-E (niet-gebouwgebonden energie, vereist) ──────────────────────
    ngebgeb_id = _guid()
    _add(entities, relations, ngebgeb_id, 'NGEBGEB-E', {
        'NGEBGEB-E_INVOER':  'NGEBGEB-E_VAST',
        'NGEBGEB-E_MAX':     '',
        'NGEBGEB-E_METHODE': 'NGEBGEB-E_EIGEN_INVOER',
        'NGEBGEB-E_MIN':     '',
        'NGEBGEB-E_OPEN':    'false',
        'NGEBGEB-E_PER_M2':  '',
        'NGEBGEB-E_VAST':    '0',
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

    # Verzamel IDs voor RESULT-entiteiten die buiten de loop aangemaakt worden
    all_unit_ids           = []
    all_prestatie_unit_ids = []
    all_rz_ids             = []
    all_unit_rz_ids        = []

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
        all_unit_ids.append(unit_id)
        all_prestatie_unit_ids.append(prestatie_unit_id)

        # ── Per Rekenzone ──────────────────────────────────────────────────────
        for rz_idx, rz in enumerate(obj.get('rekenzones', [])):
            rz_id    = _guid()
            unit_rz_id = _guid()
            all_rz_ids.append(rz_id)
            all_unit_rz_ids.append(unit_rz_id)

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

            # UNIT-RZ-GF structuur:
            #   Hoofdfunctie krijgt UNIT-RZ-GF #1 met oppervlak = Ag − som(deelfuncties).
            #   Elke deelfunctie krijgt een eigen UNIT-RZ-GF.
            #   Geen GRUIMTE aanmaken: VABI heeft geen gemeenschappelijke ruimten;
            #   BEGR en VERLZONE worden direct onder UNIT-RZ gehangen.
            deelfuncties = rz.get('deelfuncties', [])
            deelsom = sum(df['opp'] for df in deelfuncties)
            hoofdfunctie_ag = max(0.0, ag - deelsom)

            # UNIT-RZ-GF voor hoofdfunctie
            unit_rz_gf_id = _guid()
            _add(entities, relations, unit_rz_gf_id, 'UNIT-RZ-GF', {
                'UNIT-RZ-GFAG': _fmt(hoofdfunctie_ag if deelfuncties else ag),
                'UNIT-RZ-GFID': gf_code,
            })
            _link(relations, unit_rz_id, 'UNIT-RZ', unit_rz_gf_id, 'UNIT-RZ-GF')

            # Extra UNIT-RZ-GF voor elke deelfunctie (geen GRUIMTE)
            for df in deelfuncties:
                df_gf_code = HOOFDFUNCTIE_TO_GF.get(df['functie'], 'GF_BIJEENOVER')
                df_gf_id = _guid()
                _add(entities, relations, df_gf_id, 'UNIT-RZ-GF', {
                    'UNIT-RZ-GFAG': _fmt(df['opp']),
                    'UNIT-RZ-GFID': df_gf_code,
                })
                _link(relations, unit_rz_id, 'UNIT-RZ', df_gf_id, 'UNIT-RZ-GF')

            # VENTCAP wordt aangemaakt in _build_vent en daar ook aan UNIT-RZ gekoppeld
            ventcap_id = _guid()  # placeholder, wordt doorgegeven aan _build_vent

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
            verlichtingen = rz.get('verlichtingen', [])
            first_vlzone_for_gruimte = None
            for vl in verlichtingen:
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
                    'VERLZ_NWW':     '',
                    'VERLZ_OMSCHR':  vl['naam'],
                    'VERLZ_PN':      _fmt(vl['vermogen']),
                    'VERLZ_TYPE':    '',
                    'VERLZ_VERLREG': verlreg,
                    'VERLZ_WL':      '',
                })
                _link(relations, unit_rz_id, 'UNIT-RZ', vlzone_id, 'VERLZONE')

            # Fallback: altijd minimaal 1 VERLZONE per rekenzone (vereist door Uniec3)
            if not verlichtingen:
                vlzone_id = _guid()
                _add(entities, relations, vlzone_id, 'VERLZONE', {
                    'VERLZ_A':       _fmt(ag),
                    'VERLZ_DAGLREG': '',
                    'VERLZ_FD':      '',
                    'VERLZ_FD_NON':  '1,000',
                    'VERLZ_F_AFZ':   '0,00',
                    'VERLZ_KAG30':   'VERLZ_KAG_KANT_NVT',
                    'VERLZ_NWW':     '',
                    'VERLZ_OMSCHR':  rz.get('naam', 'VZ'),
                    'VERLZ_PN':      '',
                    'VERLZ_TYPE':    '',
                    'VERLZ_VERLREG': 'VERLZ_VERLREG_CA',
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

                # BEGR_HEL: 90° voor gevels, n.v.t. voor daken, leeg voor vloeren
                if vlak == 'VLAK_GEVEL':
                    begr_hel = '90'
                elif vlak == 'VLAK_DAK':
                    begr_hel = 'n.v.t.'
                else:
                    begr_hel = ''

                _add(entities, relations, begr_id, 'BEGR', {
                    'BEGR_A':          _fmt(hv['opp_bruto']),
                    'BEGR_AOR':        '',
                    'BEGR_AOS':        '',
                    'BEGR_B':          '',
                    'BEGR_DAK':        '',
                    'BEGR_DUMMY':      '',
                    'BEGR_GEVEL':      begr_gevel,
                    'BEGR_HEL':        begr_hel,
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
                    'CONSTRWG_OPP': _fmt(hv['opp_bruto']),
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
                'TAPW-UNIT-RZ_OPP': _fmt(rz.get('ag', 0.0)),
            })
            _link(relations, unit_rz_id, 'UNIT-RZ', tapw_unit_rz_id, 'TAPW-UNIT-RZ')

            # ── Installatiesystemen ─────────────────────────────────────────────
            _build_verw(entities, relations, unit_rz_id, rz_id)
            _build_vent(entities, relations, unit_id, unit_rz_id, rz_id, ventcap_id)
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

    # ── Resultaat-entiteiten (verplicht voor Uniec3) ────────────────────────────
    _build_results(
        entities, relations,
        basis_id=basis_id,
        geb_id=geb_id,
        unit_ids=all_unit_ids,
        prestatie_geb_id=prestatie_geb_id,
        prestatie_unit_ids=all_prestatie_unit_ids,
        rz_ids=all_rz_ids,
        unit_rz_ids=all_unit_rz_ids,
    )

    return entities, relations


# De 22 vaste RESULT-ENERGIEFUNCTIE sjablonen (CAT, CODE, NAAM, EENHEID, GROOTHEID)
_RESULT_EF_TEMPLATES = [
    ('RESULT_VERW', 'E',      'elektrisch',                    'kWh', 'E<sub>H;ci</sub>'),
    ('RESULT_VERW', 'GAS',    'gas',                           'kWh', 'E<sub>H;ci</sub>'),
    ('RESULT_VERW', 'OLIE',   'olie',                          'kWh', 'E<sub>H;ci</sub>'),
    ('RESULT_VERW', 'BIOM1',  'biomassa - Activiteitenbesluit', 'kWh', 'E<sub>H;ci</sub>'),
    ('RESULT_VERW', 'BIOM2',  'biomassa - NTA 8800 bijlage R', 'kWh', 'E<sub>H;ci</sub>'),
    ('RESULT_VERW', 'BIOM3',  'biomassa - overig',             'kWh', 'E<sub>H;ci</sub>'),
    ('RESULT_VERW', 'EW_VERW','externe warmtelevering',        'kWh', 'E<sub>H;ci</sub>'),
    ('RESULT_TAPW', 'E',      'elektrisch',                    'kWh', 'E<sub>W;ci</sub>'),
    ('RESULT_TAPW', 'GAS',    'gas',                           'kWh', 'E<sub>W;ci</sub>'),
    ('RESULT_TAPW', 'OLIE',   'olie',                          'kWh', 'E<sub>W;ci</sub>'),
    ('RESULT_TAPW', 'BIOM1',  'biomassa - Activiteitenbesluit', 'kWh', 'E<sub>W;ci</sub>'),
    ('RESULT_TAPW', 'BIOM2',  'biomassa - NTA 8800 bijlage R', 'kWh', 'E<sub>W;ci</sub>'),
    ('RESULT_TAPW', 'BIOM3',  'biomassa - overig',             'kWh', 'E<sub>W;ci</sub>'),
    ('RESULT_TAPW', 'EW_TAPW','externe warmtelevering',        'kWh', 'E<sub>W;ci</sub>'),
    ('RESULT_KOEL', 'E',      'elektrisch',                    'kWh', 'E<sub>C;ci</sub>'),
    ('RESULT_KOEL', 'GAS',    'gas',                           'kWh', 'E<sub>C;ci</sub>'),
    ('RESULT_KOEL', 'EW_KOEL','externe koudelevering',         'kWh', 'E<sub>C;ci</sub>'),
    ('RESULT_VENT', '',       'ventilatoren',                  'kWh', 'E<sub>V;ci</sub>'),
    ('RESULT_VERL', '',       'verlichting',                   'kWh', 'E<sub>L;ci</sub>'),
    ('RESULT_BEVO', 'E',      'elektrisch',                    'kWh', 'E<sub>hum;ci</sub>'),
    ('RESULT_BEVO', 'GAS',    'gas',                           'kWh', 'E<sub>hum;ci</sub>'),
    ('RESULT_BEVO', 'OLIE',   'olie',                          'kWh', 'E<sub>hum;ci</sub>'),
]


def _build_results(entities, relations, basis_id, geb_id, unit_ids,
                   prestatie_geb_id, prestatie_unit_ids, rz_ids, unit_rz_ids):
    """
    Maakt de verplichte resultaat-entiteiten aan die Uniec3 nodig heeft om een
    bestand te kunnen openen:
      - NTA-RESULTS + MWA-RESULTS (standalone)
      - 44× RESULT-ENERGIEFUNCTIE (22 per GEB + 22 per UNIT)
      - RESULT-ENERGIEGEBRUIK (1 per GEB + 1 per UNIT)
      - RESULT-GTO / RESULT-LSTRM / RESULT-TOJULI (1 per RZ + 1 per UNIT-RZ)
    """
    # NTA-RESULTS + MWA-RESULTS (standalone, geen parent)
    nta_id = _guid()
    _add(entities, relations, nta_id, 'NTA-RESULTS', {'NTA-RESULTS_DUMMY': ''})
    mwa_id = _guid()
    _add(entities, relations, mwa_id, 'MWA-RESULTS', {'MWA-RESULTS_DUMMY': ''})

    # NTA-RESULTS + BASIS → PRESTATIE (gebouw + unit)
    _link(relations, nta_id,   'NTA-RESULTS', prestatie_geb_id, 'PRESTATIE')
    for pid in prestatie_unit_ids:
        _link(relations, nta_id,   'NTA-RESULTS', pid, 'PRESTATIE')
        _link(relations, basis_id, 'BASIS',        pid, 'PRESTATIE')

    def _make_ef(props):
        eid = _guid()
        _add(entities, relations, eid, 'RESULT-ENERGIEFUNCTIE', props)
        return eid

    ef_props_template = {
        'RESULT-ENERGIEFUNCTIE_RESULTAAT':          '',
        'RESULT-ENERGIEFUNCTIE_RES_ENER_NONPRIM':   '',
        'RESULT-ENERGIEFUNCTIE_RES_ENER_PRIM':      '',
        'RESULT-ENERGIEFUNCTIE_RES_HULPENER_NONPRIM': '',
        'RESULT-ENERGIEFUNCTIE_RES_HULPENER_PRIM':  '',
    }

    # 22 RESULT-ENERGIEFUNCTIE onder GEB
    geb_ef_ids = []
    for cat, code, naam, eenheid, grootheid in _RESULT_EF_TEMPLATES:
        props = dict(ef_props_template,
                     **{'RESULT-ENERGIEFUNCTIE_CAT': cat,
                        'RESULT-ENERGIEFUNCTIE_CODE': code,
                        'RESULT-ENERGIEFUNCTIE_NAAM': naam,
                        'RESULT-ENERGIEFUNCTIE_EENHEID': eenheid,
                        'RESULT-ENERGIEFUNCTIE_GROOTHEID': grootheid})
        eid = _make_ef(props)
        geb_ef_ids.append(eid)
        _link(relations, geb_id, 'GEB', eid, 'RESULT-ENERGIEFUNCTIE')
        _link(relations, basis_id, 'BASIS', eid, 'RESULT-ENERGIEFUNCTIE')
        _link(relations, nta_id, 'NTA-RESULTS', eid, 'RESULT-ENERGIEFUNCTIE')

    # 22 RESULT-ENERGIEFUNCTIE per UNIT
    for unit_id in unit_ids:
        for cat, code, naam, eenheid, grootheid in _RESULT_EF_TEMPLATES:
            props = dict(ef_props_template,
                         **{'RESULT-ENERGIEFUNCTIE_CAT': cat,
                            'RESULT-ENERGIEFUNCTIE_CODE': code,
                            'RESULT-ENERGIEFUNCTIE_NAAM': naam,
                            'RESULT-ENERGIEFUNCTIE_EENHEID': eenheid,
                            'RESULT-ENERGIEFUNCTIE_GROOTHEID': grootheid})
            eid = _make_ef(props)
            _link(relations, unit_id, 'UNIT', eid, 'RESULT-ENERGIEFUNCTIE')
            _link(relations, basis_id, 'BASIS', eid, 'RESULT-ENERGIEFUNCTIE')
            _link(relations, nta_id, 'NTA-RESULTS', eid, 'RESULT-ENERGIEFUNCTIE')

    # RESULT-ENERGIEGEBRUIK: 1 voor GEB + 1 per UNIT
    def _empty_energiegebruik():
        return {k: '' for k in [
            'RESULT-BOIM_GEBGEB', 'RESULT-CO2_CO2', 'RESULT-ELEKTR_GEBGEB',
            'RESULT-ELEKTR_NIETGEBGEB', 'RESULT-ELEKTR_OPGEWEKT', 'RESULT-ELEKTR_TOT',
            'RESULT-EP_HERNIEUWBARE_ENERGIE_INDICATOR',
            'RESULT-EP_HERNIEUWBARE_ENERGIE_INDICATOR_EMG_FORF',
            'RESULT-EP_WARMTEBEHOEFTE', 'RESULT-EWEK_EK', 'RESULT-EWEK_EW',
            'RESULT-GAS_GEBGEB', 'RESULT-HERNIEUW_ELEKTR', 'RESULT-HERNIEUW_KOEL',
            'RESULT-HERNIEUW_TAPW', 'RESULT-HERNIEUW_TOT', 'RESULT-HERNIEUW_TOT_EMGFORF',
            'RESULT-HERNIEUW_VERW', 'RESULT-KARAKT_TOT', 'RESULT-KOSTEN_ELEKTR',
            'RESULT-NETTO_WARMTEVRAAG', 'RESULT-OLIE_TOT', 'RESULT-OPP_GEBROPP',
            'RESULT-OPP_VERLOPP', 'RESULT-OPP_VORMFACTOR',
            'RESULT_KARAKT_OPGEW_E', 'RESULT_KARAKT_SOM_EPEH',
        ]}

    eg_geb_id = _guid()
    _add(entities, relations, eg_geb_id, 'RESULT-ENERGIEGEBRUIK', _empty_energiegebruik())
    _link(relations, geb_id,    'GEB',         eg_geb_id, 'RESULT-ENERGIEGEBRUIK')
    _link(relations, basis_id,  'BASIS',        eg_geb_id, 'RESULT-ENERGIEGEBRUIK')
    _link(relations, nta_id, 'NTA-RESULTS', eg_geb_id, 'RESULT-ENERGIEGEBRUIK')

    for unit_id in unit_ids:
        eg_unit_id = _guid()
        _add(entities, relations, eg_unit_id, 'RESULT-ENERGIEGEBRUIK', _empty_energiegebruik())
        _link(relations, unit_id,  'UNIT',         eg_unit_id, 'RESULT-ENERGIEGEBRUIK')
        _link(relations, basis_id, 'BASIS',         eg_unit_id, 'RESULT-ENERGIEGEBRUIK')
        _link(relations, nta_id, 'NTA-RESULTS', eg_unit_id, 'RESULT-ENERGIEGEBRUIK')

    # Per-RZ resultaten: RESULT-GTO, RESULT-LSTRM, RESULT-TOJULI
    for rz_id, unit_rz_id in zip(rz_ids, unit_rz_ids):
        # RESULT-GTO
        gto_rz_id = _guid()
        _add(entities, relations, gto_rz_id, 'RESULT-GTO', {k: '' for k in [
            'RESULT-GTO_FCTRL', 'RESULT-GTO_SPUIVENT_QVARGLIN',
            'RESULT-GTO_SPUIVENT_QVARGLOUT', 'RESULT-GTO_ZNVENT_QVARGLLIN',
            'RESULT-GTO_ZNVENT_QVARGLLOUT',
        ]})
        _link(relations, rz_id,     'RZ',      gto_rz_id, 'RESULT-GTO')
        _link(relations, basis_id,  'BASIS',    gto_rz_id, 'RESULT-GTO')
        _link(relations, nta_id,  'NTA-RESULTS', gto_rz_id, 'RESULT-GTO')

        gto_urz_id = _guid()
        _add(entities, relations, gto_urz_id, 'RESULT-GTO', {k: '' for k in [
            'RESULT-GTO_FCTRL', 'RESULT-GTO_SPUIVENT_QVARGLIN',
            'RESULT-GTO_SPUIVENT_QVARGLOUT', 'RESULT-GTO_ZNVENT_QVARGLLIN',
            'RESULT-GTO_ZNVENT_QVARGLLOUT',
        ]})
        _link(relations, unit_rz_id, 'UNIT-RZ', gto_urz_id, 'RESULT-GTO')
        _link(relations, basis_id,   'BASIS',    gto_urz_id, 'RESULT-GTO')
        _link(relations, nta_id,   'NTA-RESULTS', gto_urz_id, 'RESULT-GTO')

        # RESULT-LSTRM (1 per RZ, 1 per UNIT-RZ)
        lstrm_rz_id = _guid()
        _add(entities, relations, lstrm_rz_id, 'RESULT-LSTRM', {k: '' for k in [
            'RESULT-LSTRM_LEAINZIMI', 'RESULT-LSTRM_SUPZIMI', 'RESULT-LSTRM_VENTINZIMI',
        ]})
        _link(relations, rz_id,    'RZ',      lstrm_rz_id, 'RESULT-LSTRM')
        _link(relations, basis_id, 'BASIS',    lstrm_rz_id, 'RESULT-LSTRM')
        _link(relations, nta_id, 'NTA-RESULTS', lstrm_rz_id, 'RESULT-LSTRM')

        lstrm_urz_id = _guid()
        _add(entities, relations, lstrm_urz_id, 'RESULT-LSTRM', {k: '' for k in [
            'RESULT-LSTRM_LEAINZIMI', 'RESULT-LSTRM_SUPZIMI', 'RESULT-LSTRM_VENTINZIMI',
        ]})
        _link(relations, unit_rz_id, 'UNIT-RZ', lstrm_urz_id, 'RESULT-LSTRM')
        _link(relations, basis_id,   'BASIS',    lstrm_urz_id, 'RESULT-LSTRM')
        _link(relations, nta_id,   'NTA-RESULTS', lstrm_urz_id, 'RESULT-LSTRM')

        # RESULT-TOJULI
        # RESULT-TOJULI_AANW_AANV_BER is a required input field (not auto-calculated);
        # must be pre-set to 'RESULT-TOJULI_AANW_AANV_BER1', otherwise [D001] is raised.
        _tojuli_props = dict({k: '' for k in [
            'RESULT-TOJULI_BEP_ZON', 'RESULT-TOJULI_KOELCAP', 'RESULT-TOJULI_MAX',
            'RESULT-TOJULI_NOORD', 'RESULT-TOJULI_NOORD_OOST', 'RESULT-TOJULI_NOORD_WEST',
            'RESULT-TOJULI_OOST', 'RESULT-TOJULI_RAAMFACTOR', 'RESULT-TOJULI_WEINIG_RAMEN',
            'RESULT-TOJULI_WEST', 'RESULT-TOJULI_ZUID', 'RESULT-TOJULI_ZUID_OOST',
            'RESULT-TOJULI_ZUID_WEST', 'RESULT_TOJULI_RISICO', 'RESULT_TOJULI_TYPE_KOEL',
        ]}, **{'RESULT-TOJULI_AANW_AANV_BER': 'RESULT-TOJULI_AANW_AANV_BER1'})
        tojuli_rz_id = _guid()
        _add(entities, relations, tojuli_rz_id, 'RESULT-TOJULI', _tojuli_props)
        _link(relations, rz_id,    'RZ',      tojuli_rz_id, 'RESULT-TOJULI')
        _link(relations, basis_id, 'BASIS',    tojuli_rz_id, 'RESULT-TOJULI')
        _link(relations, nta_id, 'NTA-RESULTS', tojuli_rz_id, 'RESULT-TOJULI')

        tojuli_urz_id = _guid()
        _add(entities, relations, tojuli_urz_id, 'RESULT-TOJULI', _tojuli_props)
        _link(relations, unit_rz_id, 'UNIT-RZ', tojuli_urz_id, 'RESULT-TOJULI')
        _link(relations, basis_id,   'BASIS',    tojuli_urz_id, 'RESULT-TOJULI')
        _link(relations, nta_id,   'NTA-RESULTS', tojuli_urz_id, 'RESULT-TOJULI')


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
        'VERW_OPEN': 'true',
        'VERW_OPM':  '',
        'VERW_XXXX': '',
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
        'VERW-DISTR_AANV_POMP': 'VERW-DISTR_AANV_POMP_WEL',
        'VERW-DISTR_AAN_LAGEN': '2',
        'VERW-DISTR_ONTW':      'VERW-DISTR_ONTW_GE32_D',
        'VERW-DISTR_POMP_INV':  'VERW-DISTR_POMP_INV_D',
        'VERW-DISTR_TYPE':      'VERW-DISTR_TYPE_C',
        'VERW-DISTR_WAT':       'VERW-DISTR_WAT_W',
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


def _build_vent(entities, relations, unit_id, unit_rz_id, rz_id, ventcap_id):
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
        'VENT_FCTRL':   '1,10',
        'VENT_GEM':     'VENT_GEM_NIET',
        'VENT_INVOER':  'VENT_FORF',
        'VENT_LBK':     'VENT_LBK_WEL',
        'VENT_OPEN':    'true',
        'VENT_OPM':     '',
        'VENT_OPP_GEM': '',
        'VENT_OPP_LBK': '',
        'VENT_PKOEL':   'VENTDIS_PKOEL_AUTO',
        'VENT_SYS':     'VENTSYS_NATMECH',
        'VENT_SYSVAR':  '10',
        'VENT_VARIANT': 'VARIANT_C2a',
        'VENT_VERB':    '',
        'VENT_VERBL':   '',
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

    # VENTILATOR #1 onder VENT, VENTILATOR #2 onder VENTAAN
    for parent_id, parent_type in [(vent_id, 'VENT'), (ventaan_id, 'VENTAAN')]:
        ventilator_id = _guid()
        _add(entities, relations, ventilator_id, 'VENTILATOR', {})
        _link(relations, parent_id, parent_type, ventilator_id, 'VENTILATOR')

        veig_id = _guid()
        _add(entities, relations, veig_id, 'VENTILATOREIG', {})
        _link(relations, ventilator_id, 'VENTILATOR', veig_id, 'VENTILATOREIG')
        _link(relations, unit_id, 'UNIT', veig_id, 'VENTILATOREIG')  # 2e ouder

    # WARMTETERUG #1 onder VENT, #2 onder VENTAAN; elk met WARMTE-TOEV-KAN (ouders: WARMTETERUG + UNIT)
    for parent_id, parent_type in [(vent_id, 'VENT'), (ventaan_id, 'VENTAAN')]:
        wtr_id = _guid()
        _add(entities, relations, wtr_id, 'WARMTETERUG', {})
        _link(relations, parent_id, parent_type, wtr_id, 'WARMTETERUG')

        wtk_id = _guid()
        _add(entities, relations, wtk_id, 'WARMTE-TOEV-KAN', {})
        _link(relations, wtr_id,  'WARMTETERUG', wtk_id, 'WARMTE-TOEV-KAN')
        _link(relations, unit_id, 'UNIT',         wtk_id, 'WARMTE-TOEV-KAN')  # 2e ouder

    # VENT-VERB #1: ouders VENT + UNIT; VENT-VERB #2: ouders VENTAAN + UNIT
    for parent_id, parent_type in [(vent_id, 'VENT'), (ventaan_id, 'VENTAAN')]:
        verb_id = _guid()
        _add(entities, relations, verb_id, 'VENT-VERB', {})
        _link(relations, parent_id, parent_type, verb_id, 'VENT-VERB')
        _link(relations, unit_id,   'UNIT',        verb_id, 'VENT-VERB')  # 2e ouder

    # VENTDIS
    ventdis_id = _guid()
    _add(entities, relations, ventdis_id, 'VENTDIS', {
        'VENTDIS_C':     'VENTDIS_C_BUI',
        'VENTDIS_CKOEL': 'VENTDIS_CKOEL_GEEN',
        'VENTDIS_CVERW': 'VENTDIS_CVERW_GEEN',
        'VENTDIS_DICHT': 'VENTDIS_DICHT_ONB',
        'VENTDIS_LBK':   'VENTDIS_LBK_D_A',
    })
    _link(relations, vent_id, 'VENT', ventdis_id, 'VENTDIS')

    # VENTDEB → VENTZBR (niet VENT → VENTZBR!)
    ventdeb_id = _guid()
    _add(entities, relations, ventdeb_id, 'VENTDEB', {
        'VENTDEB_CAP':    'VENTDEBCAP_ONB',
        'VENTDEB_CAPTAB': '',
        'VENTDEB_ZBR':    '',
        'VENTDEB_ZBRTAB': '',
    })
    _link(relations, vent_id, 'VENT', ventdeb_id, 'VENTDEB')

    ventzbr_id = _guid()
    _add(entities, relations, ventzbr_id, 'VENTZBR', {
        'VENTZBR_AANW': 'False',
        'VENTZBR_AG':   '',
    })
    _link(relations, ventdeb_id, 'VENTDEB', ventzbr_id, 'VENTZBR')  # ouder = VENTDEB
    _link(relations, rz_id,      'RZ',       ventzbr_id, 'VENTZBR')  # 2e ouder

    # VENTCAP: gekoppeld aan zowel UNIT-RZ (via caller) als VENT (hier)
    _add(entities, relations, ventcap_id, 'VENTCAP', {
        'VENTCAP_MD': '', 'VENTCAP_MV': '', 'VENTCAP_NAOS': '',
        'VENTCAP_ND': '', 'VENTCAP_NV': '',
    })
    _link(relations, unit_rz_id, 'UNIT-RZ', ventcap_id, 'VENTCAP')
    _link(relations, vent_id,    'VENT',    ventcap_id, 'VENTCAP')

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
        'KOEL-OPWEK_FABR':   'KOEL-OPWEK_FABR_GR',
        'KOEL-OPWEK_GEM':    'KOEL-OPWEK_GEM_NIET',
        'KOEL-OPWEK_INVOER': 'KOEL-OPWEK_INVOER_FORF',
        'KOEL-OPWEK_TYPE':   'KOEL-OPWEK_TYPE_1',
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
        'KOEL-DISTR_POMP_INV':  'KOEL-DISTR_POMP_INV_D',
        'KOEL-DISTR_VERDAMP':   'KOEL-DISTR_VERDAMP_3',
        'KOEL-DISTR_WAT':       'KOEL-DISTR_WAT_6',
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
        'INSTALLATIONS-FORM_DUMMY': '',
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
            'Afmeldstatus':  0,
            'CreateDate':    _now(),
        }]
        z.writestr('buildings.json', json.dumps(buildings, ensure_ascii=False))

        # entities.json + relations.json + deltas.json + summary.json
        bid = _BUILD_ID
        z.writestr(f'buildings/{bid}/entities.json',
                   json.dumps(entities, ensure_ascii=False, indent=2))
        z.writestr(f'buildings/{bid}/relations.json',
                   json.dumps(relations, ensure_ascii=False, indent=2))
        z.writestr(f'buildings/{bid}/deltas.json',
                   json.dumps([], ensure_ascii=False))
        # Minimale summary
        geb_naam = vabi.get('naam', filename)
        summary = {
            'BuildingId':    _BUILD_ID,
            'GEB_OMSCHR':    geb_naam,
            'GEB_TYPEGEB':   'TGEB_UTILIT',
            'GEB_SRTBW':     'NIEUWB',
            'GEB_HASMELD':   'False',
            'GEB_CALCNEEDED': 'false',
            'RZFORM_CALCUNIT': 'RZUNIT_GEB',
        }
        z.writestr(f'buildings/{bid}/summary.json',
                   json.dumps(summary, ensure_ascii=False))

    return buf.getvalue()
