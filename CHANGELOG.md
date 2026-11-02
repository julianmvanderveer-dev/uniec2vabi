# Changelog — Uniec2Vabi

Versiegeschiedenis van het Uniec2Vabi project (Brynt.nl). Nieuwste versie bovenaan.

---

## v51 — 2026-11-02 — (pending)
**Commit:** PV ZonneEnergieList (K), Douche-WTW (L), fossiele brandstoffen vinkje (M)
**Wijzigingen:**
- K: ZonneEnergieList per installatie gevuld vanuit PV/PV-VELD entiteiten (Uniec3 → VABI)
  - Per-woning PV: PV-VELD gekoppeld aan UNIT → in installatie voor dat rekenzonetype
  - Per-gebouw PV: PV-VELD alleen gekoppeld aan PV → in alle installaties
  - OppervlakPaneel, AantalPanelen, Hellingshoek, Orientatie, Bouwintegratie, WattpiekvermogenPVPanelen, InvoerBeschaduwing ingevuld
- L: TapwaterDwtwList per tapwatersysteem gevuld vanuit TAPW-DOUCHE entiteit
  - DwtwAanwezig=1 en AantalDouches gevuld als TAPW-DOUCHE aanwezig
  - AantalAangeslotenDouches, AansluitwijzeDWTW, TypeDWTW, Rendement ingevuld
  - Altijd één TapwaterDwtw item (ook als leeg, conform VABI-referentie)
- M: GeenFossieleBrandstoffenOpPerceel toegevoegd aan ObjectClassificatie
  - Primair: OVERIG_INST_CO2_EM == OVERIG_CO2_EM_NIET → geen fossiel
  - Fallback: verwarming+tapwater niet op gas/WKK/stadsverwarming → geen fossiel
- Bugfix: parse_uniec3 aanroep _process_unit had onjuist 4e argument (inst_guid)

---

## v50 — 2026-11-02 — 8bb0d8e
**Commit:** Installaties per rekenzonetype via UNIT-RZID (J)
**Wijzigingen:**
- J: Eén VABI-installatie per uniek UNIT-RZID (rekenzonetype A/B/...) i.p.v. één per project
- Amaliahoff (9 woningen, zelfde type): 1 installatie gedeeld door 9 rekenzones
- 2-rekenzone project (type A + B): 2 aparte installaties
- Installaties benoemd "Installatie 1", "Installatie 2", etc.

---

## v49 — 2026-11-02 — 373b6c5
**Commit:** Fix maten vlakken (H) en ψ-waarden 3 decimalen (I)
**Wijzigingen:**
- H: Breedte en HoogteOfLengte hoofdvlakken gevuld vanuit BEGR_B / BEGR_L (was hardcoded 0)
- H: Breedte en HoogteOfLengte deelvlakken gevuld vanuit CONSTRT_B / CONSTRT_L (was hardcoded 0)
- I: PsiWaarde koudebruggen nu 3 decimalen i.p.v. 2 (bijv. 0.040 i.p.v. 0.04)

---

## v48 — 2026-10-30 — f81c7b4
**Commit:** Fix bouwlagen/verdiepingen: AantalBouwlagenRekenzone en GO per laag vanuit UNIT-RZBLAAG
**Wijzigingen:**
- AantalBouwlagenRekenzone: leest UNIT-RZBLAAG uit Uniec3 (was hardcoded 1; nu bv. 3)
- Verdiepingen: genereert N Verdieping-entries elk met GO/N (was 1 entry met totale GO)
- Dakraam oriëntatie/hellingshoek: deelvlakken in hellend dak erven correct Ori+Hel van het hoofdvlak (gefikst als bijeffect van D; BEGR_DAK/BEGR_HEL werkt nu ook voor dakramen)

---

## v47 — 2026-10-30 — 81388a2
**Commit:** Fix Invoer, hellend dak (locatie/hellingshoek/oriëntatie) en UGlas/UKozijn
**Wijzigingen:**
- Invoer constructies: '5' → '4' (conform VABI 11.x EPA referentieformaat; '5' werd in VABI weergegeven als leeg veld)
- UGlas en UKozijn altijd 0: Uw totaal (ramen/deuren) zit in het <U>-veld; dubbele invulling verwijderd
- Hellend dak Locatie: naam-gebaseerde override (voor/achter) geldt nu alleen voor VLAK_GEVEL, niet voor VLAK_DAK/VLAK_VLOER; VLAK_DAK krijgt altijd Locatie=1 (Dak)
- Hellend dak Hellingshoek: leest BEGR_HEL uit Uniec3 (bv. 40°) i.p.v. hardcoded 0
- Hellend dak Oriëntatie: leest BEGR_DAK compass (DAK_BTNL_W→2, DAK_BTNL_O→6 etc.) i.p.v. standaard 0

---

## v46 — 2026-06-29 — d6d25ad
**Commit:** Fix GrenstAan kruipruimte: VL_MV_KR→3, niet 1 (1=water in VABI)
**Wijzigingen:**
- GrenstAan=1 is water in VABI, niet kruipruimte; kruipruimte is 3
- VL_MV_KR teruggezet naar GrenstAan=3; eerdere wijziging naar 1 was gebaseerd op foute referentie-EPA
- VL_MV_WA (water, komt niet voor in Uniec) ook op 3 gezet als fallback naar kruipruimte

---

## v45 — 2026-06-29 — 0829b03
**Commit:** Fix opnamedatum (NL-tijd +2) en GrenstAan water → kruipruimte
**Wijzigingen:**
- Opnamedatum: gebruik NL-tijd (CEST +2) in plaats van UTC; 20260622 was correct, niet 20260621
- VL_MV_WA (vloer grenst aan water) bestaat niet in Uniec; mapping gewijzigd naar kruipruimte (GrenstAan=1) als fallback

---

## v44 — 2026-06-29 — d6ce8d3
**Commit:** Fix Bouwfase, Opnamedatum, GrenstAan, ConstructieType, Qv10 en zijbelemmering (uniec3→vabi)
**Wijzigingen:**
- Bouwfase: leest GEB_SRTBW uit Uniec (NIEUWB→1, bestaande bouw→0) i.p.v. hardcoded 0
- Opnamedatum: parse GEB_DATE als UTC ISO-8601 en geef terug als YYYYMMDD
- GrenstAan vloer: leest BEGR_VLOER/BEGR_VLOER_BOVBUI subtype → VABI-waarde (kruipruimte=1, grond=4, water=3, buitenlucht=0)
- ConstructieType: LIBVLAK_VLOER_BOVBUI→7 (buitenplafond/overkraging was foutief 0)
- Qv10Gemeten/Waarde: leest INFIL_INVOER=INFIL_MWA en INFILUNIT_QV per unit via UNIT→INFILUNIT relatie
- InvoerBeschaduwing=7 (automatisch) voor BELEMM_ZIJ_*=BELEMM_ZIJBELEMML_REKEN
- vabi_to_uniec3: corrigeert HOOFDFUNCTIE_TO_GF naar geldige Uniec3 GF codes

---

## v43 — 2026-05-22 — 4267eb7
**Commit:** Fix factuur download: toon altijd als invoice_nr gezet is (ook bij promocode)
**Wijzigingen:**
- Factuur downloadknop verschijnt nu ook wanneer een promocode is gebruikt en invoice_nr beschikbaar is

---

## v42 — 2026-05-22 — af5a596
**Commit:** Fix zijbelemmering: InvoerBeschaduwing 2→6, Zijbelemmering kant, RelatieveBreedteZijbelemmering
**Wijzigingen:**
- InvoerBeschaduwing voor zijbelemmering gecorrigeerd van 2 naar 6
- Zijbelemmering kant correct ingelezen
- RelatieveBreedteZijbelemmering correct overgenomen

---

## v41 — 2026-05-22 — 7c974f6
**Commit:** Fix ConstructieType dak/vloer, Opname detailopname, Invoer Rc-waarde
**Wijzigingen:**
- ConstructieType voor dak en vloer correct gemapped
- Opname detailopname correct verwerkt
- Invoer Rc-waarde correctie doorgevoerd

---

## v40 — 2026-05-22 — b1961f5
**Commit:** Fix hellingshoek/begrenzing/overstek/adres in deelvlak en hoofdvlak
**Wijzigingen:**
- Hellingshoek correct overgenomen in deelvlak en hoofdvlak
- Begrenzing correct gemapped
- Overstek correct ingelezen
- Adres correct overgenomen

---

## v39 — 2026-05-20 — 9339949
**Commit:** Fix: n.v.t. beschaduwing → InvoerBeschaduwing=-1 (niet 0)
**Wijzigingen:**
- InvoerBeschaduwing bij "niet van toepassing" beschaduwing gecorrigeerd naar -1 (was foutief 0)

---

## v38 — 2026-05-20 — ab1d4c6
**Commit:** Fix woningpositie, opnamedatum, registratiestatus en belemmering (uniec3→vabi)
**Wijzigingen:**
- Woningpositie correct overgenomen uit Uniec3
- Opnamedatum correct verwerkt
- Registratiestatus correct gemapped
- Belemmering correct ingelezen

---

## v37 — 2026-05-07 — 8f912d5
**Commit:** Fix ventilatie-structuur conform Uniec3 referentie
**Wijzigingen:**
- Ventilatie-XML-structuur aangepast conform de Uniec3 referentiespecificatie

---

## v36 — 2026-05-07 — 695bce0
**Commit:** Fix: voeg verplichte Uniec3-entiteiten toe zodat bestand opent
**Wijzigingen:**
- Verplichte Uniec3-entiteiten toegevoegd die ontbraken waardoor het bestand niet opende

---

## v35 — 2026-05-06 — d03e598
**Commit:** Voeg VABI EPA → Uniec3 converter toe voor utiliteitsgebouwen
**Wijzigingen:**
- Nieuwe converter richting toegevoegd: VABI EPA naar Uniec3 (voor utiliteitsgebouwen)

---

## v34 — 2026-04-27 — c95d8d8
**Commit:** Fix: meerdere EPA-compatibiliteitsproblemen opgelost
**Wijzigingen:**
- Diverse EPA-compatibiliteitsproblemen in de conversie opgelost

---

## v33 — 2026-04-23 — ffea6d5
**Commit:** Fix: VLAK_VLOER_BOVBUI Locatie waarde 7 is ongeldig in VABI EPA
**Wijzigingen:**
- Locatiewaarde 7 voor VLAK_VLOER_BOVBUI vervangen door geldige waarde

---

## v32 — 2026-04-23 — 66fc68c
**Commit:** Fix: EffectieveInterneWarmtecapaciteit ontbrak in Rekenzone Algemeen
**Wijzigingen:**
- EffectieveInterneWarmtecapaciteit toegevoegd aan Rekenzone Algemeen

---

## v31 — 2026-04-23 — 47846d1
**Commit:** Fix: ConstructieType 6/7 ongeldig voor dak/vloer constructies
**Wijzigingen:**
- ConstructieType 6 en 7 gecorrigeerd voor dak- en vloerconstructies naar geldige waarden

---

## v30 — 2026-04-23 — 31a4fbb
**Commit:** Voeg promotiecode toe om betaling te omzeilen
**Wijzigingen:**
- Promotiecode-functionaliteit toegevoegd waarmee betaling omzeild kan worden

---

## v29 — 2026-04-08 — 6f83b77
**Commit:** Feature: meerdere rekenzones per gebouw
**Wijzigingen:**
- Ondersteuning voor meerdere rekenzones per gebouw toegevoegd

---

## v28 — 2026-04-08 — b35203c
**Commit:** Fix: installatie-type detectie + disclaimer verwijderd
**Wijzigingen:**
- Installatie-type detectie gecorrigeerd
- Disclaimer verwijderd

---

## v27 — 2026-04-08 — ff4440d
**Commit:** Fix: gebouwhoogte (INFIL_BGH) nu correct overgenomen in VABI EPA
**Wijzigingen:**
- Gebouwhoogte uit INFIL_BGH wordt nu correct doorgezet naar VABI EPA

---

## v26 — 2026-04-08 — ce90b45
**Commit:** Test: Invoer=0 voor alle constructietypen (ook ramen/deuren)
**Wijzigingen:**
- Invoer=0 ingesteld voor alle constructietypen inclusief ramen en deuren (testcommit)

---

## v25 — 2026-04-08 — 53461d2
**Commit:** Fix: _fmt altijd 2 decimalen, XmlVersie naar 11.2.2 om VABI-upgrade bij openen te voorkomen
**Wijzigingen:**
- _fmt-functie geeft nu altijd 2 decimalen terug
- XmlVersie verlaagd naar 11.2.2 om ongewenste VABI-upgrade bij openen te voorkomen

---

## v24 — 2026-04-08 — 02cc230
**Commit:** Fix: Invoer=0+Rc voor opaque, UKozijn voor deuren — waarden stabiel bij klikken in VABI
**Wijzigingen:**
- Invoer=0 met Rc voor opaque constructies
- UKozijn correct ingesteld voor deuren
- Waarden blijven stabiel bij klikken in VABI

---

## v23 — 2026-04-08 — 7964161
**Commit:** Fix: Rc zichtbaar via Invoer=4 opaque, Uw via UGlas ramen; deur-beperking in disclaimer
**Wijzigingen:**
- Rc zichtbaar via Invoer=4 voor opaque constructies
- Uw zichtbaar via UGlas voor ramen
- Deur-beperking toegelicht in disclaimer

---

## v22 — 2026-04-08 — a1ff2aa
**Commit:** Fix ZIP create_system=3 (Unix) + herstel Invoer=2/Bron=1 conform referentie
**Wijzigingen:**
- ZIP create_system gecorrigeerd naar 3 (Unix) voor compatibiliteit
- Invoer=2 en Bron=1 hersteld conform referentieproject

---

## v21 — 2026-04-08 — 1794d2d
**Commit:** Schakel naar Invoer=4/Bron=2 formaat conform echt VABI-importproject (W25.004)
**Wijzigingen:**
- Invoer=4 en Bron=2 formaat overgenomen conform echt VABI-importproject W25.004

---

## v20 — 2026-04-07 — 92f2804
**Commit:** Fix constructies: UGlas=0.00 voor alle typen, IsolatieAanwezig=1 voor opaque
**Wijzigingen:**
- UGlas=0.00 ingesteld voor alle constructietypen
- IsolatieAanwezig=1 voor opaque constructies

---

## v19 — 2026-04-07 — f446010
**Commit:** Disclaimer: Rc- en Uw-waarden nog niet correct in VABI, wordt aan gewerkt
**Wijzigingen:**
- Disclaimer toegevoegd dat Rc- en Uw-waarden nog niet correct zijn in VABI

---

## v18 — 2026-04-07 — 70e794b
**Commit:** Fix Rc/U-waarden zichtbaar in VABI: Invoer=2, UGlas voor transparante constructies
**Wijzigingen:**
- Rc/U-waarden zichtbaar gemaakt in VABI via Invoer=2
- UGlas gebruikt voor transparante constructies

---

## v17 — 2026-04-07 — 90cc505
**Commit:** Invoer=4 Bron=2 zelfde als echte VABI-bestanden, IsolatieAanwezig=-1, ProdG=0
**Wijzigingen:**
- Invoer=4 en Bron=2 conform echte VABI-bestanden
- IsolatieAanwezig=-1 en ProductinformatieGWaarde=0 ingesteld

---

## v16 — 2026-04-07 — 65d9bc5
**Commit:** Gebouwhoogte terugzetten naar 0.00 (VABI berekent zelf)
**Wijzigingen:**
- Gebouwhoogte teruggezet naar 0.00 zodat VABI zelf berekent

---

## v15 — 2026-04-07 — 21141b3
**Commit:** Gebouwhoogte lezen uit INFIL_BGH
**Wijzigingen:**
- Gebouwhoogte wordt nu uitgelezen uit INFIL_BGH veld

---

## v14 — 2026-04-07 — f295843
**Commit:** Fix Invoer=2 voor Rc/U-invoer, IsolatieAanwezig en ProductinformatieGWaarde
**Wijzigingen:**
- Invoer=2 correct ingesteld voor Rc/U-invoer
- IsolatieAanwezig correct ingesteld
- ProductinformatieGWaarde correct verwerkt

---

## v13 — 2026-04-07 — 66833d3
**Commit:** Fix oriëntatie, bruto/netto opp, U/g-waarden, Rc-persistentie, gebouwhoogte
**Wijzigingen:**
- Oriëntatie correct overgenomen
- Bruto en netto oppervlak correct berekend
- U- en g-waarden correct doorgezet
- Rc-waarden persistent gemaakt
- Gebouwhoogte correct verwerkt

---

## v12 — 2026-04-04 — 72f06d7
**Commit:** Tekst gratis woning aangepast
**Wijzigingen:**
- Tekst over gratis woning bijgewerkt op welkomstscherm

---

## v11 — 2026-04-04 — a425d33
**Commit:** Webhook triggert conversie bij bevestigde betaling (browser hoeft niet terug)
**Wijzigingen:**
- Mollie webhook verwerkt nu de conversie direct bij bevestigde betaling
- Gebruiker hoeft browser niet terug te sturen naar de site

---

## v10 — 2026-04-04 — 13d7d54
**Commit:** Wachttijdmelding verwijderd, emailadres info@brynt.nl toegevoegd
**Wijzigingen:**
- Wachttijdmelding verwijderd
- Emailadres info@brynt.nl toegevoegd aan de interface

---

## v9 — 2026-04-04 — c845bdb
**Commit:** Gratis woning, BengCert en alternatief betalen vermeld op welkomstscherm
**Wijzigingen:**
- Gratis woning optie vermeld op welkomstscherm
- BengCert vermeld
- Alternatieve betalingsmethode vermeld

---

## v8 — 2026-04-01 — 701b1c4
**Commit:** Factuur vermelding toegevoegd aan welkomstscherm
**Wijzigingen:**
- Vermelding van factuurmogelijkheid toegevoegd aan het welkomstscherm

---

## v7 — 2026-04-01 — 60a1098
**Commit:** ADMIN_KEY uit config verwijderd, via environment variable
**Wijzigingen:**
- ADMIN_KEY niet meer hardcoded in config, wordt nu geladen via environment variable

---

## v6 — 2026-04-01 — 1948bb8
**Commit:** Bedrijfsgegevens Borgch B.V. ingevuld
**Wijzigingen:**
- Bedrijfsgegevens van Borgch B.V. ingevuld in de applicatie

---

## v5 — 2026-04-01 — 255e6c2
**Commit:** Facturen, success pagina, klantgegevens, juridische disclaimer, admin
**Wijzigingen:**
- Factuurpagina toegevoegd
- Success pagina toegevoegd
- Klantgegevens formulier toegevoegd
- Juridische disclaimer toegevoegd
- Admin-paneel toegevoegd

---

## v4 — 2026-04-01 — 239b4d8
**Commit:** Preview na upload + 1 woning gratis converteren
**Wijzigingen:**
- Preview van het Uniec-bestand na upload toegevoegd
- Eerste woning gratis te converteren

---

## v3 — 2026-04-01 — 21c35b7
**Commit:** Wachttijd melding toegevoegd aan navbar
**Wijzigingen:**
- Wachttijdmelding toegevoegd aan de navigatiebalk

---

## v2 — 2026-04-01 — c31598b
**Commit:** Verwijder start.bat (bevat lokale sleutel)
**Wijzigingen:**
- start.bat verwijderd uit de repository (bevatte lokale sleutel, niet geschikt voor versiecontrole)

---

## v1 — 2026-04-01 — c563f40
**Commit:** Initiële versie Uniec2Vabi — Brynt.nl
**Wijzigingen:**
- Eerste werkende versie van de Uniec2Vabi conversietool
- Basisconversie van Uniec3-bestanden naar VABI EPA-formaat
- Webinterface via Flask (Brynt.nl)
