# ─────────────────────────────────────────────────────────────────────────────
# Uniec2Vabi — instellingen
# ─────────────────────────────────────────────────────────────────────────────

# Prijs
PRICE_PER_DWELLING = 10.00   # Euro per woning (excl. BTW)
VAT_RATE           = 0.21    # BTW-tarief (21%)
FREE_UP_TO         = 1       # Bestanden met max. dit aantal woningen zijn gratis

# Bedrijfsgegevens (voor facturen)
BEDRIJF_NAAM        = 'Borgch B.V.'
BEDRIJF_HANDELSNAAM = 'Brynt.nl'
BEDRIJF_ADRES       = 'Oranjelaan 3G1'
BEDRIJF_POSTCODE    = '3311 DH'
BEDRIJF_PLAATS      = 'Dordrecht'
BEDRIJF_KVK         = '81091516'
BEDRIJF_BTW         = 'NL861924824B01'
BEDRIJF_IBAN        = 'NL23 INGB 0005 9885 80'
BEDRIJF_EMAIL       = 'info@borgch.nl'

# Admin (factuuroverzicht) — stel in als Render environment variable ADMIN_KEY
ADMIN_KEY = ''

# Promotiecode(s) — kommagescheiden, stel in als Render environment variable BYPASS_CODES
# Voorbeeld: BYPASS_CODES=INTERN,BENGCERT2024
BYPASS_CODES = 'ORANJELAAN3G!'
