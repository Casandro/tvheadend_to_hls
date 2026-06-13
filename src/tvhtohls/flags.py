"""Best-effort country-of-origin guesser for TVHeadend channels.

Public API:
    guess_country(channel, services_by_uuid=None) -> 'XX' | None
    flag_emoji(iso) -> '🇩🇪' or ''

The heuristic tries (in order): explicit country marker in the channel name,
curated broadcaster name patterns, provider/network field, channel-tag names,
and finally the audio-stream language of any linked service.
"""
import re


def flag_emoji(iso):
    if not iso or len(iso) != 2 or not iso.isalpha():
        return ""
    iso = iso.upper()
    # Regional Indicator Symbol Letter A is U+1F1E6 (= 0x1F1E6 = 127462)
    return chr(0x1F1E6 + ord(iso[0]) - ord("A")) + chr(0x1F1E6 + ord(iso[1]) - ord("A"))


_COUNTRY_NAMES = {
    "DE": "Germany", "AT": "Austria", "CH": "Switzerland",
    "GB": "United Kingdom", "IE": "Ireland",
    "FR": "France", "MC": "Monaco",
    "IT": "Italy", "ES": "Spain", "PT": "Portugal",
    "NL": "Netherlands", "BE": "Belgium", "LU": "Luxembourg",
    "PL": "Poland", "CZ": "Czechia", "SK": "Slovakia", "HU": "Hungary",
    "RO": "Romania", "BG": "Bulgaria",
    "HR": "Croatia", "RS": "Serbia", "ME": "Montenegro", "SI": "Slovenia",
    "AL": "Albania", "BA": "Bosnia and Herzegovina",
    "LT": "Lithuania", "LV": "Latvia", "EE": "Estonia",
    "AM": "Armenia", "AZ": "Azerbaijan", "GE": "Georgia", "TJ": "Tajikistan",
    "SN": "Senegal", "GQ": "Equatorial Guinea",
    "GR": "Greece", "TR": "Turkey", "CY": "Cyprus",
    "SE": "Sweden", "FI": "Finland", "NO": "Norway", "DK": "Denmark",
    "RU": "Russia", "UA": "Ukraine", "BY": "Belarus",
    "US": "United States", "CA": "Canada", "VE": "Venezuela", "CU": "Cuba",
    "JP": "Japan", "KR": "South Korea", "CN": "China", "HK": "Hong Kong",
    "TW": "Taiwan", "ID": "Indonesia",
    "QA": "Qatar", "IL": "Israel",
    "IR": "Iran", "IQ": "Iraq", "LB": "Lebanon", "SY": "Syria",
    "SA": "Saudi Arabia", "AE": "United Arab Emirates",
    "OM": "Oman", "BH": "Bahrain", "YE": "Yemen", "JO": "Jordan",
    "EG": "Egypt", "TN": "Tunisia", "SD": "Sudan", "DZ": "Algeria",
    "MA": "Morocco", "LY": "Libya",
    "VA": "Vatican City",
}


def country_name(iso):
    if not iso:
        return ""
    return _COUNTRY_NAMES.get(iso.upper(), iso.upper())


# (regex pattern, country code). Patterns are case-insensitive, applied with re.search.
# Order matters: more specific patterns must come before broader ones so
# e.g. "Phoenix InfoNews" → HK wins over plain "Phoenix" → DE.
_NAME_PATTERNS = [
    # ── Specific overrides that must precede their broader patterns ──────────
    # Italian RTL 102.5 (radio network) — distinct from German RTL.
    (r"\bRTL\s*102", "IT"),
    # Hong Kong Phoenix Television (must precede German "Phoenix").
    (r"\bPhoenix\s+(?:Info|CNE|HK|Hong\s*Kong|Chinese|North\s*America)", "HK"),
    # Romanian Prima TV (the Czech "Prima COOL/Plus" is a separate ID).
    (r"\bPrima\s+TV\b", "RO"),
    # Czech Nova family (Nova S is Serbian, comes later).
    (r"\bNova S\b", "RS"),
    # Italian sub-channels of Arte (different operator from Franco-German "arte").
    (r"\bArte\s+(Atelier|Investimenti)\b", "IT"),
    # Polish local radio "Radio VOX FM" (e.g. "3140 Poznan VOX") — distinct from German VOX.
    (r"^\d{4}\s+(?:\w+\s+)?VOX\b", "PL"),
    # Strong city-name signal: any channel with "Berlin" in the name is German.
    (r"\bBerlin\b", "DE", 3),
    # Luxembourgish channels (override RTL → DE for the Lëtzebuerg feed).
    (r"\bL[ëe]tzebuerg\b|\bLuxembourg\b", "LU", 3),
    # Nordic Kanal 10 — must precede the generic Turkish KANAL\s*\d+ pattern below.
    (r"\bKanal 10 Norge\b", "NO", 3),
    (r"\bKanal 10 Sverige\b", "SE", 3),

    # ── Germany ──────────────────────────────────────────────────────────────
    (r"\bARD(?!\w)", "DE"),
    (r"\bARD[- ]alpha\b", "DE"),
    (r"\bZDF\b", "DE"),
    (r"\b3sat\b", "DE"),
    (r"\barte\b", "DE"),
    (r"\bDas Erste\b", "DE"),
    (r"\bPhoenix\b", "DE"),
    (r"\bTagesschau24\b", "DE"),
    (r"\bRTL(?!.*\bNL\b)", "DE"),   # plain RTL = DE
    (r"\bSat\.?1\b", "DE"),
    (r"\bProSieben\b|\bPro7\b", "DE"),
    (r"\bKabel ?eins\b", "DE"),
    (r"\bVOX\b", "DE"),
    (r"\bMDR\b|\bNDR\b|\bWDR\b|\bSWR\b|\bBR\b|\bhr-fernsehen\b|\brbb\b", "DE"),
    (r"\bKiKA\b", "DE"),
    (r"\bn-tv\b|\bWelt\b", "DE"),
    (r"\bDW\b", "DE"),  # Deutsche Welle
    (r"\b1-2-3\.tv\b", "DE"),
    (r"\bDMAX(?!\s*Aus)", "DE"),    # plain DMAX = DE; DMAX Austria handled below
    (r"\bHSE\b", "DE"),
    (r"\bQVC\b", "DE"),
    (r"\bJuwelo\b|\bsonnenklar\b|\bMediaShop\b|\bGenius (TV|Exklusiv|Family|Trend)\b", "DE"),
    (r"\bDFB\b|\bdeluxe\b|\bDOKUSAT\b|\bDie Neue Zeit\b", "DE"),
    (r"\bEurosport \d+ Deutschland\b", "DE"),
    (r"\bBaden TV\b|\bNIEDERBAYERN\b|\bSACHSEN\b|\btv\.ingolstadt\b|\brfo Regional\b", "DE"),
    (r"\bMelodie TV\b|\bSchlager Deluxe\b|\bVolksmusik\b|\bSTIMMUNGSGARTEN\b|\bHöhenrausch\b", "DE"),
    (r"\bRiC NEU\b|\bSPORT1\b|\bDF1\b|\bN24\b|\bzdf_neo\b|\bZDFinfo\b", "DE"),
    (r"\bEROTIKA\b|\bBabestation\b|\bEroticsat\b|\bDreamgirls\b|\bHandystar\b|\bHeisse?\b|\bSex-Kontakte\b|\bSexy (Girls|Treff)\b|\bTraumfrauen\b|\bTeleSex\b|\bGayBoys\b|\bMobile Sex\b|\bHot Babes\b", "DE"),
    (r"\bL-TV\b|\bL ?TV\b|\bbibel ?TV\b|\bGOD ?TV\b|\bEWTN katholisches\b", "DE"),
    (r"\bTELEGOLD\b", "DE"),
    (r"\bSENDER NEU JERUSALEM\b|\bONE TERRA\b|\bWOTSCH\b", "DE"),
    (r"\bSPIRIT TV\b|\bSerien\+\b|\bNEO\b|\bSuper\+\b|\bsixx(?!\s*AUSTRIA)\b", "DE"),
    (r"\bDeluxe (Music|Dance|Rap|Lounge|Rock|Flashback)\b|\bdeluxe music\b", "DE"),
    (r"\bHype TV\b|\bredpath\b|\bExpres S\b|\bSpirit\b|\bHGTV\b|\bHome\+\b", "DE"),
    (r"\bsuper\+\b|\bWarner TV\b", "DE"),
    (r"\bVOXup\b|\bXplore\b|\bZwei Music\b|\ba\.tv\b|\bsixx\b", "DE"),
    (r"\bAnixe\b", "DE"),
    (r"\bDMF\b|\bCrime Time\b|\bJust (Cooking|Fishing)\b|\bSERIEN\+|\bSerien\+|\bDYN\b", "DE"),
    (r"\bDreamgirls24\b|\bEroticsat24\b|\bNeuer Sender\b", "DE"),
    # `m.nchen` — TVHeadend sometimes mojibakes the umlaut in "münchen.tv" → "mþnchen.tv"
    (r"\bLILO\.?TV\b|\bLokal-TV-Portal\b|\bm.?nchen\.tv\b|\bOTVA\b|\bFranken Plus\b", "DE"),
    (r"\bWRN English Europe\b|\bWRN Russkij\b", "GB"),  # World Radio Network is UK-based

    # ── Austria ──────────────────────────────────────────────────────────────
    (r"\bORF\d*\b", "AT"),
    (r"\bKabel ?1 Austria\b|\bkabel1 Doku austria\b|\bDMAX Austria\b|\bsixx Austria\b|\bTLC Austria\b", "AT"),
    (r"\boe24\b|\bkrone\.tv\b|\bR9 Oesterreich\b|\bLT1-OOE\b|\bTV1 OOE\b|\bK-TV\b", "AT"),
    (r"\bNITRO Austria\b|\bntv Austria\b|\b\w+\s+Austria\b", "AT"),
    (r"\bServus TV\b", "AT"),

    # ── Switzerland ──────────────────────────────────────────────────────────
    (r"\bSRF\d*\b|\bSF[12]\b", "CH"),

    # ── UK ───────────────────────────────────────────────────────────────────
    (r"\bBBC\b", "GB", 3),
    (r"\bITV\b", "GB"),
    (r"\bChannel ?[45]\b", "GB"),
    (r"\bSky News\b|\bSky Sports\b", "GB"),
    (r"\bITN\b", "GB"),

    # ── France ───────────────────────────────────────────────────────────────
    (r"\bTF1\b", "FR"),
    (r"\bFrance [2345]\b|\bFrance ?24\b", "FR"),
    (r"\bM6\b|\bW9\b|\bTFX\b", "FR"),
    (r"\bCanal\+", "FR"),
    (r"\bBFM\b|\bLCI\b|\bLCP\b|\bCNEWS\b", "FR"),
    (r"\bTV5MONDE?\b|\bTV5\b(?!\s*Monaco)", "FR"),
    (r"\bICI [A-Z]", "FR"),  # ICI Belfort-Montbéliard etc. — French regional public
    (r"\bRCF\b|\bRFM\b|\bNRJ\b|\bFRANCE INTER\b|\bFIP\b|\bKTO\b", "FR"),
    (r"\bVIAVOSGES\b|\bProvence\b|\bFRANSAT\b|\bPUBLIRAD\b|\bBARKER COLLECTIVITES\b|\bPANNEAU ARRET\b", "FR"),
    (r"\bTV[VS] Infokanaal\b", "FR"),

    # ── Monaco ───────────────────────────────────────────────────────────────
    (r"\bTVMonaco\b|\bTV5 Monaco\b", "MC"),
    (r"\bTMC\b", "FR"),  # TMC is French-operated now

    # ── Italy ────────────────────────────────────────────────────────────────
    (r"\bRAI\b", "IT"),
    (r"\bMediaset\b", "IT"),
    (r"\bItalia ?1\b|\bCanale ?5\b|\bRete ?4\b|\bLa ?7\b", "IT"),
    (r"\bCanale Italia\b|\bCANALE ITALIA\b", "IT"),
    (r"\bCanzoni Napoletane\b|\bCusano Napoli\b|\bL Unione TV\b", "IT"),
    (r"\bRadio (Italia|Kiss Kiss)\b|\bRDS\b|\bRADIONORBA\b|\bInBlu\b|\binBlu\b", "IT"),
    (r"\bSardegna\b|\bONDASARDA\b|\bVIDEOLINA\b|\bTelesardegna\b|\bT\.C\.S\b", "IT"),
    (r"\bSolo Calcio\b|\bSNAI\b|\bPrimafila\b|\bTelecupole\b|\bTeleregione\b|\bTVA Vicenza\b|\bTeleradiopace\b|\bTELENEWS\b", "IT"),
    (r"\bItaly Service\b|\bDocumentari\b|\bBambini\b|\bIntrattenim\b|\bACISPORT\b|\bParole di Vita\b|\bPadre Pio\b", "IT"),
    (r"\bSan Marino RTV\b", "IT"),
    (r"\bANTENNA SUD\b|\bFOGGIA TV\b|\bCANALE 7\b|\bClass TV Moda\b|\btivù la guida\b|\bCamera Deputati\b|\bAGON Channel\b", "IT"),

    # ── Spain ────────────────────────────────────────────────────────────────
    (r"\bTVE\b|\bLa ?[12]\b", "ES"),
    (r"\bAntena ?3\b|\bTelecinco\b|\bCuatro\b|\blaSexta\b|\bNeox\b", "ES"),
    (r"\bARAGON\b", "ES"),
    (r"\bDKISS\b|\bDivinity\b|\bBe Mad\b|\bTeledeporte\b|\bTRECE\b|\bCANAL SUR\b|\bCATALAN TV\b|\bPORTADA\b|\bReal Madrid TV\b|\bBAJO DEMANDA\b", "ES"),
    (r"\bBoing\b", "ES"),
    (r"\bAtreseries\b|\bAtresmedia\b|\bClan\b|\bEnergy\b", "ES"),

    # ── Portugal ─────────────────────────────────────────────────────────────
    (r"\bRTP\b", "PT"),

    # ── Netherlands ──────────────────────────────────────────────────────────
    (r"\bNPO ?[123]\b|\bSBS6\b|\bNet5\b|\bBVN\b", "NL"),

    # ── Belgium ──────────────────────────────────────────────────────────────
    (r"\bVRT\b|\béén\b|\bCanvas\b|\bRTBF\b|\bLa Une\b", "BE"),

    # ── Poland ───────────────────────────────────────────────────────────────
    (r"\bTVP\b|\bPolsat\b|\bPolonia\b|\bTVN\b", "PL"),
    # Polish ANTENA (channel literally named ANTENA, no trailing digit).
    (r"\bANTENA\b(?!\s*3)", "PL"),
    (r"\bESKA\b|\bSuperNova\b|\bERock\b|\bPLUS\b(?=.*\bPoland\b)|\bRadio Rodzina\b|\bEmaus\b|\bRadio PLUS\b", "PL"),
    (r"\bTV Trwam\b|\bDla Ciebie TV\b|\bSZLAGIER\b|\bNUTA (GOLD|TV)\b|\bPolo TV\b|\bEnter Film\b", "PL"),
    # Specifically Polish-city + PLUS pattern (Radio PLUS local stations)
    (r"^\d{4}\s+\w+\s+PLUS\b", "PL"),

    # ── Czechia ──────────────────────────────────────────────────────────────
    (r"\bČT\b|\bCT ?\d+\b", "CZ"),
    (r"\bNova\b", "CZ"),    # Nova S already overridden above to RS
    (r"\bOcko\b|\bOCKO\b|\bPrima\b", "CZ"),

    # ── Slovakia ─────────────────────────────────────────────────────────────
    (r"\bRTVS\b|\bTV Lux\b|\bMetropola\b", "SK"),

    # ── Hungary ──────────────────────────────────────────────────────────────
    (r"\bM[1-9](?!\d)\b|\bDuna\b", "HU"),

    # ── Romania ──────────────────────────────────────────────────────────────
    (r"\bTVR\b", "RO"),
    (r"\bDIGI ?24\b|\bPRO TV\b|\bPRO CINEMA\b|\bPRO ARENA\b", "RO"),
    (r"\bRomaniaTV\b|\bRealitatea\b|\bRadio Romania\b", "RO"),
    (r"\bACASA\b|\bFavorit\s*(TV|FM)\b|\bRadio Vocea Sperantei\b|\bSperantaTV\b|\bCredo TV\b", "RO"),

    # ── Bulgaria ─────────────────────────────────────────────────────────────
    (r"\bBNT\d*\b|\bbTV\b|\bBTV\b|\bBulgaria on Air\b|\bRadio Focus Trakia\b|\bDevin\b", "BG"),

    # ── Croatia ──────────────────────────────────────────────────────────────
    (r"\bHRT\b|\bDM-SAT\b", "HR"),

    # ── Serbia ───────────────────────────────────────────────────────────────
    (r"\bRTS\b|\bK::CN\b|\bSONCE\b", "RS"),

    # ── Montenegro ───────────────────────────────────────────────────────────
    (r"\bTVCG\b", "ME"),

    # ── Bosnia and Herzegovina ───────────────────────────────────────────────
    (r"\bBHTV\b|\bBHT[123]?\b", "BA", 4),  # 4 to beat tied alphabetical with BG (VIVACOM)

    # ── Lithuania / Latvia / Estonia ─────────────────────────────────────────
    (r"\bLRT\b|\bLithuanica\b", "LT"),

    # ── Armenia / Azerbaijan / Tajikistan ────────────────────────────────────
    (r"\bARMPUB\b|\bShant\b|\bArmenia TV\b", "AM"),
    (r"\bCBC Azerbaijan\b|\bAzTV\b|\bİdman Azerbaycan\b", "AZ"),
    (r"\bTJK\s*tv\b", "TJ"),

    # ── Slovenia ─────────────────────────────────────────────────────────────
    (r"\bSLO ?[23]\b|\bNova24\b", "SI"),

    # ── Albania ──────────────────────────────────────────────────────────────
    (r"\bRTSH\b|\bRadio Tirana\b|\bEuronews Albania\b", "AL"),

    # ── Greece ───────────────────────────────────────────────────────────────
    (r"\bERT\b|\bSkai\b|\bMEGA\b|\bEDESSA\b|\bΒΟΥΛΗ\b|\bΠΕΙΡΑΙΚΗ\b", "GR"),

    # ── Turkey (and Turkish-language) ─────────────────────────────────────────
    (r"\bTRT\b", "TR"),
    (r"\bTV8\b", "TR"),
    (r"\bATV\s*AVRUPA\b|\bBEYAZ\b|\bDREAM\s*T", "TR"),
    (r"\bHABERTURK\b|\bHABER\s*GLOBAL\b|\bHALK\s*TV\b|\bHT\s*SPOR\b", "TR"),
    (r"\bKanal\s*D\b|\bKANAL\s*[BTV]\b|\bKANAL\s*\d+\b|\bKANAL\s*(AVRUPA|FIRAT|URFA|JADID|YEK)\b", "TR"),
    (r"\bTGRT\b|\bRUMELI\b|\bSHOW\s*TURK\b|\bSTAR\s*TV\b|\bTELE\s*[15]\b", "TR"),
    (r"\bFB\s*TV\b|\bPOWERTURK\b|\bNR1\s*TURK\b|\bTYT\s*TURK\b|\bTH\s*TURKHABER\b", "TR"),
    (r"\bMELTEM\b|\bMERCAN\b|\bKON\s*TV\b|\bKOY\s*TV\b|\bKOZA\s*TV\b|\bGENC\s*TV\b|\bGRT\s*TV\b", "TR"),
    (r"\bKARDELEN\b|\bKADIRGA\b|\bLALEGUL\b|\bORDU\s*ALTAS\b|\bORLER\b|\bGUNEYDOGU\b", "TR"),
    (r"\bSEMERKAND\b|\bREHBER\s*TV\b|\bGONCA\s*TV\b|\bGOZDE\s*TV\b|\bCAGRI\s*FM\b|\bCAY\s*TV\b", "TR"),
    (r"\bSAT7TURK\b|\bULUSAL\s*1\b|\bVAV\s*TV\b|\bVAN65\b|\bMALATYA\b|\bMEDYA\s*HABER\b", "TR"),
    (r"\bEKOL\b|\bEKOTURK\b|\bEGEMAX\b|\bEGE\s*TV\b|\bD\s*anadolu\b|\bBRTV\s*ANADOLU\b", "TR"),
    (r"\bBENG.\s*T.RK\b|\bBI\s*KANAL\b|\bB.\s*KANAL\b|\bEKİNTÜRK\b|\bRADYO\s*7\b|\bPAL\s*(NOSTALJI|STATION)\b", "TR"),
    (r"\bSHOW\s*TURK\b|\bTAL\s*TV\b|\bTARIM\s*ORMAN\b|\bTHT\b|\bTURKSAT\b|\bTuso\b", "TR"),
    (r"^A\s+(HABER|NEWS|PARA)(\s+HD)?$", "TR"),
    (r"\bA\s+SPOR\b|\bA2\b", "TR"),
    (r"\bFLASH\s*HABER\b|\bFM\s*TV\b|\bEURO\s*D\b|\bEUROSTAR\b|\bMC\s*EU\b|\bSRS\s*-\s*TV\b", "TR"),
    (r"\bSMTV\b|\bYILDIZ\s*EN\b|\bSTOON\s*TV\b|\bTEK\s*RUMELI\b|\bSTERK\s*TV\b|\bZAD\s*TV\b", "TR"),
    (r"\bDOST\s*TV\b|\bGZT\b|\bGNC\b|\bGO-TV\b|\bbtq\b|\bgu fil\b|\bGU\b|\b24\s*kanal\b|\bUlusal\b", "TR"),
    (r"\bTVNET\b", "TR"),

    # Cyprus / Turkish Cyprus
    (r"\bKIBRIS\s*TV\b|\bBRT\s*[123]\b", "CY"),

    # ── Ukraine ──────────────────────────────────────────────────────────────
    (r"\bSuspilne\b|\b1\+1\b|\b2\+2\b|\bMarafon\b|\bPRYAMIY\b|/+PRYAMIY", "UA"),
    (r"\bICTV\b|\bSTB\b|\bTET\b|\bInter\b|\bK1\b(?!\s*KARDELEN)|\bNOVY\b|\bKvartal\b", "UA"),
    (r"\bEspreso\b|\bSVOBODA\b|\bUNIAN\b|\bUATV\b|\bWE-UKRAINE\b|\bTVIY SERIAL\b", "UA"),
    (r"\bGALYCHYNA\b|\bKYIV\b|\bROZPAKUY\b|\bNADIYA\b|\bPlus Plus\b|\bRADA TV\b|\bArmy TV\b|\bDIM TV\b", "UA"),
    (r"\bFREEDOM\b|\bSvit\+\b|\bUNIAN\b|\bWE-UKRAINE\b|\bTBN Ukraine\b|\b24 Kanal\b", "UA"),
    (r"\bPERETS INTERNATIONAL\b|\bíKRA\b|\bNTN\b(?!\s*INT)", "UA"),

    # ── Russia ───────────────────────────────────────────────────────────────
    (r"\bRossiya\b|\bChannel One\b|\bPervyy\b|\bRTR PLANETA\b|\bRUSSIA ?24\b", "RU"),
    (r"\bNTV (MIR|PRAVO|SERIAL|STYLE)\b", "RU"),
    (r"\bIZVESTIYA\b|\bDOMASHNIY\b|\bRTVI\b|\bSTS INTERNATIONAL\b|\bTBN Russia\b", "RU"),
    (r"\bEuronews Russia\b|\bNastoyashcheye Vremya\b|\bRussia.s Future\b|\bRTR\b", "RU"),

    # Belarus
    (r"\bBelarus Tomorrow\b|\bEuropean Radio for Belarus\b|\bRTB\b", "BY"),

    # ── Iran (Persian-language diaspora and domestic) ────────────────────────
    (r"\bManoto\b|\bPars TV\b|\bIran International\b|\bIRANEFARDA\b|\bIRNR\b", "IR"),
    (r"\bSimay(e)? Azadi\b|\bKalemeh\b|\bAVANG\b|\bKanal Yek\b|\bKanal Jadid\b", "IR"),
    (r"\bPBC Tapesh\b|\bPersiana\b|\bIran E Aryee\b|\bErfan halgheh\b|\bRadio Javan\b|\bRadio Jahani\b", "IR"),
    (r"\bATRINA\b|\bAVA\s*Max\d?\b|\bAVA\b|\bVilayet TV\b|\bGalaxy4\b", "IR"),

    # ── Iraqi Kurdistan / Iraq ───────────────────────────────────────────────
    (r"\bRUDAW\b|\bKurdistan 24\b|\bKRT TV\b|\bRonahi\b|\bJIN TV\b|\bJEK TV\b|\bMed TV\b|\bZAROK\b|\bLUYS TV\b", "IQ"),
    (r"\bAl-Forat\b|\bAL MALAKOOT\b|\bThe Kingdom Sat\b|\bTurkmeneli\b", "IQ"),

    # ── Levant / Gulf / Arab ─────────────────────────────────────────────────
    (r"\bAl[- ]?Jazeera\b", "QA"),
    (r"\bQatar TV\b", "QA"),
    (r"\bSaudi (Ch|TV|SUNNAH)\b|\bMBC\b|\bAl Ekhbaria\b|\bSBC\b", "SA"),
    (r"\bDubai\b|\bABUDHABI\b|\bAbu\s*Dhabi\b", "AE"),
    (r"\bOMAN\b", "OM"),
    (r"\bBAHRAIN\b", "BH"),
    (r"\bYemen\b", "YE"),
    (r"\bJORDAN\b", "JO"),
    (r"\bSUDAN\b|\bTELE SAHEL\b|\bTELE CONGO\b|\bRADIO CONGO\b", "SD"),
    (r"\bTunisie\b", "TN"),
    (r"\bCanal Algerie\b|\bAlgerie\b|\bAlgeria\b|\bTamazight\b|\bTV TAMAZIGHT\b|\bTV 4 Tamazigh\b", "DZ"),
    (r"\b2M MONDE\b", "MA"),
    (r"\bVatican Media\b", "VA"),
    (r"\bLibya\b|\bLAAYOUNE\b", "LY"),
    (r"\bNoursat\b|\bAl Mayadeen\b", "LB"),
    (r"\bShams tv\b|\bJordan TV\b|\bMTA3\b", "JO"),

    # ── Senegal ──────────────────────────────────────────────────────────────
    (r"\bTFM Senegal\b", "SN"),

    # ── Equatorial Guinea ────────────────────────────────────────────────────
    (r"\bTVGE\b", "GQ"),

    # ── East Asia ─────────────────────────────────────────────────────────────
    (r"\bNHK\b", "JP"),
    (r"\bKBS\b|\bEBS\+?\b", "KR"),
    (r"\bCGTN\b|\bCCTV\b", "CN"),
    (r"\bNTDTV\b", "TW"),
    (r"\bTVRI\b", "ID"),

    # ── Latin America ────────────────────────────────────────────────────────
    (r"\bTelesur\b|\bteleSUR\b", "VE"),
    (r"\bCubavision\b", "CU"),

    # ── USA ──────────────────────────────────────────────────────────────────
    (r"\bCNN\b", "US"),
    (r"\bMSNBC\b|\bNBC\b|\bABC\b|\bCBS\b|\bHBO\b|\bESPN\b|\bFox News\b", "US"),
    (r"\bBloomberg\b|\bCNBC\b(?!-E)|\bOne America News\b|\bSBN International\b|\bDAYSTAR\b|\bTBN\b|\bSonlife\b|\bWildTV\b|\bMaria Vision\b|\bHGTV\b", "US"),
    # Pan-European brands carried under local providers — weak (weight 1) so
    # a stronger provider signal can override (e.g. Disney Channel on
    # BetaDigital → DE, Comedy Central on BASIS 1 → DE).
    (r"\bDisney Channel\b|\bMTV\b(?!\s*Movement)|\bComedy Central\b|\bNick\b|\bCartoon\b|\bShop LC\b|\bShopLC\b", "US", 1),

    # Israel
    (r"\bIBA\b|\bKan\b|\bChannel 1[03]\b", "IL"),

    # ── Ireland ──────────────────────────────────────────────────────────────
    (r"\bRTÉ\b|\bRTE\b", "IE"),

    # ── Scandinavia ──────────────────────────────────────────────────────────
    (r"\bSVT\b|\bSverigekanalen\b|\bKanal 10 Sverige\b|\bVision Sverige\b", "SE"),
    (r"\bYLE\b", "FI"),
    (r"\bNRK\b|\bVisjon Norge\b|\bKanal 10 Norge\b|\bBedehuskanalen\b", "NO"),
    (r"\bDR\b", "DK"),
]

# Compile patterns; each entry is (pat_str, cc) → weight 2, or (pat_str, cc, weight).
_NAME_PATTERNS = [
    (re.compile(item[0], re.IGNORECASE), item[1], item[2] if len(item) > 2 else 2)
    for item in _NAME_PATTERNS
]


# Provider-name patterns work the same way but match against the channel's
# pre-computed provider string (joined service providers, e.g. "BetaDigital, ORS").
# Multiple provider matches accumulate (additive scoring).
_PROVIDER_PATTERNS = [
    # ── Germany ──────────────────────────────────────────────────────────────
    (r"\bBetaDigital\b", "DE", 3),
    (r"\bBASIS\s*1\b", "DE", 3),
    (r"\bMTV Networks Europe\b", "DE", 3),
    (r"\bRTL Deutschland\b", "DE", 3),
    (r"\bARD\b", "DE", 3),
    (r"\bZDF\b", "DE", 3),
    (r"\bBR\b", "DE", 3),
    (r"\bMDR\b|\bNDR\b|\bWDR\b|\bSWR\b|\bhr\b|\brbb\b", "DE", 3),
    (r"\bProSiebenSat\.?1\b", "DE", 3),
    (r"\bSky Deutschland\b", "DE", 3),
    (r"\bBMT\b|\bBayerische Medien Technik\b", "DE", 3),
    # ── Austria ──────────────────────────────────────────────────────────────
    (r"\bORF\b", "AT", 3),
    (r"\bORS\b", "AT", 2),
    (r"\bServus\s*TV\b", "AT", 3),
    # ── Switzerland ──────────────────────────────────────────────────────────
    (r"\bSRG\b|\bSSR\b|\bSRF\b", "CH", 3),
    # ── UK ───────────────────────────────────────────────────────────────────
    (r"\bBBC\b", "GB", 3),
    # ── France ───────────────────────────────────────────────────────────────
    (r"\bGLOBECAST\b", "FR", 2),
    (r"\bGroupe TF1\b|\bFrance Télévisions\b", "FR", 3),
    # ── Italy ────────────────────────────────────────────────────────────────
    (r"\bMediaset\b", "IT", 3),
    (r"\bTelespazio\b", "IT", 1),  # IT-based but distributes many non-IT channels
    # ── Spain ────────────────────────────────────────────────────────────────
    (r"\bMovistar\+?\b", "ES", 3),
    (r"\bTSA\b", "ES", 1),  # ambiguous distribution code — many countries, low weight
    (r"\bRTVE\b", "ES", 3),
    # ── Poland ───────────────────────────────────────────────────────────────
    (r"\bCYFRA\+?\b", "PL", 3),
    (r"\bTVP\b|\bPolsat\b", "PL", 3),
    # ── Turkey ───────────────────────────────────────────────────────────────
    (r"\bTURK\s*MEDYA\b", "TR", 3),
    (r"\bDigitTurk\b", "TR", 3),
    (r"\bTRT\b", "TR", 3),
    (r"\bTURKSAT\b", "TR", 3),
    (r"\bGUC\s*TELEKOM\s*MEDYA\b", "TR", 3),
    (r"\bDEMIROREN\s*MEDYA\b", "TR", 3),
    # ── Italy ────────────────────────────────────────────────────────────────
    (r"\bRai\s*Way\b|\bRAI\b", "IT", 3),
    (r"\bSkyItalia\b", "IT", 3),
    # ── Spain ────────────────────────────────────────────────────────────────
    (r"\bDIGITAL\s*\+|\bDigital\s*\+", "ES", 3),  # Movistar+ predecessor
    # ── Morocco ──────────────────────────────────────────────────────────────
    (r"\bSNRT\b", "MA", 3),
    # ── Croatia ──────────────────────────────────────────────────────────────
    (r"\bT-HT\b", "HR", 3),
    # ── Switzerland ──────────────────────────────────────────────────────────
    (r"\bKABELIO\b", "CH", 2),
    # ── Slovakia ─────────────────────────────────────────────────────────────
    (r"\bTowercom\b", "SK", 3),
    # ── Greece ───────────────────────────────────────────────────────────────
    (r"\bOTE\b", "GR", 3),
    # ── Ukraine ──────────────────────────────────────────────────────────────
    (r"\b1\s*plus\s*1\s*Media\b|\b1\+1\s*Media\b", "UA", 3),
    (r"\bStarLightMedia\b", "UA", 3),
    (r"\bSvoboda Satellite\b", "UA", 1),
    (r"\bCOSMONOVA\b", "UA", 3),
    # ── Russia ───────────────────────────────────────────────────────────────
    (r"\bGazprom-Media\b|\bNMG\b", "RU", 3),
    # ── Slovakia / Central Europe ────────────────────────────────────────────
    (r"\bM7\s*Group\b|\bM7\b", "SK", 3),  # Skylink platform — Slovak/Czech
    # ── Bulgaria ─────────────────────────────────────────────────────────────
    (r"\bVIVACOM\b", "BG", 3),
    # ── Nordic ───────────────────────────────────────────────────────────────
    # Telenor is Norwegian but operates throughout the Nordics — add to both
    # so it boosts the regional signal; name patterns (Norge / Sverige / DR)
    # disambiguate the specific country.
    (r"\bTelenor\b", "NO", 2),
    (r"\bTelenor\b", "SE", 2),
    # ── Gulf / Arab ──────────────────────────────────────────────────────────
    (r"\bDU\b", "AE", 3),
    (r"\bEtisalat\b", "AE", 3),
]
_PROVIDER_PATTERNS = [
    (re.compile(item[0], re.IGNORECASE), item[1], item[2] if len(item) > 2 else 2)
    for item in _PROVIDER_PATTERNS
]

# Trailing or parenthesized two-letter country marker at the end of the name.
# Authoritative when present.
_TRAILING_CC = re.compile(
    r"(?:\s|\()(UK|US|DE|FR|IT|ES|AT|CH|NL|BE|PL|GR|TR|RU|UA|JP|KR|QA|CZ|SK|HU|PT|IE|SE|FI|NO|DK|HR|RS|BG|RO|IL)\)?\s*$",
    re.IGNORECASE,
)
_TRAILING_CC_FIX = {"UK": "GB"}  # UK isn't a valid ISO 3166-1 alpha-2; GB is

# Trailing "(Country Name)" — full country name in parens at the end of the
# channel name. Strongest possible signal (the user explicitly tagged origin).
_PAREN_COUNTRY_TO_CC = {
    "ukraine": "UA", "germany": "DE", "deutschland": "DE", "france": "FR",
    "italy": "IT", "italia": "IT", "spain": "ES", "españa": "ES",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB",
    "usa": "US", "united states": "US",
    "russia": "RU", "poland": "PL", "polska": "PL",
    "greece": "GR", "turkey": "TR", "türkiye": "TR",
    "austria": "AT", "österreich": "AT",
    "switzerland": "CH", "schweiz": "CH", "suisse": "CH",
    "netherlands": "NL", "nederland": "NL", "belgium": "BE",
    "portugal": "PT", "hungary": "HU", "magyarország": "HU",
    "czech": "CZ", "czechia": "CZ", "česko": "CZ",
    "slovakia": "SK", "slovensko": "SK",
    "romania": "RO", "românia": "RO", "bulgaria": "BG",
    "croatia": "HR", "hrvatska": "HR",
    "serbia": "RS", "slovenia": "SI", "albania": "AL",
    "bosnia": "BA", "bosnia and herzegovina": "BA",
    "sweden": "SE", "sverige": "SE", "norway": "NO", "norge": "NO",
    "denmark": "DK", "danmark": "DK", "finland": "FI", "suomi": "FI",
    "ireland": "IE", "israel": "IL", "japan": "JP", "korea": "KR",
    "china": "CN", "hong kong": "HK", "belarus": "BY",
    "luxembourg": "LU", "monaco": "MC", "cuba": "CU", "venezuela": "VE",
    "saudi": "SA", "saudi arabia": "SA",
    "uae": "AE", "egypt": "EG", "morocco": "MA", "tunisia": "TN",
    "algeria": "DZ", "libya": "LY",
    "iran": "IR", "iraq": "IQ", "lebanon": "LB", "syria": "SY",
    "jordan": "JO", "qatar": "QA", "yemen": "YE",
    "oman": "OM", "bahrain": "BH",
    "indonesia": "ID", "taiwan": "TW",
}
_PAREN_COUNTRY = re.compile(
    r"\(("
    + "|".join(sorted((re.escape(n) for n in _PAREN_COUNTRY_TO_CC), key=len, reverse=True))
    + r")\)\s*$",
    re.IGNORECASE,
)

# Audio stream language → country. Skip ambiguous ones (eng, spa, por, ara, zho, fas).
_LANG_TO_CC = {
    "deu": "DE", "ger": "DE", "de": "DE",
    "fra": "FR", "fre": "FR", "fr": "FR",
    "ita": "IT", "it": "IT",
    "nld": "NL", "dut": "NL", "nl": "NL",
    "pol": "PL", "pl": "PL",
    "ces": "CZ", "cze": "CZ", "cs": "CZ",
    "slk": "SK", "slo": "SK", "sk": "SK",
    "hun": "HU", "hu": "HU",
    "ron": "RO", "rum": "RO", "ro": "RO",
    "hrv": "HR", "hr": "HR",
    "srp": "RS", "sr": "RS",
    "bul": "BG", "bg": "BG",
    "ell": "GR", "gre": "GR", "el": "GR",
    "tur": "TR", "tr": "TR",
    "jpn": "JP", "ja": "JP",
    "kor": "KR", "ko": "KR",
    "rus": "RU", "ru": "RU",
    "ukr": "UA", "uk": "UA",
    "swe": "SE", "sv": "SE",
    "fin": "FI", "fi": "FI",
    "nor": "NO", "no": "NO",
    "dan": "DK", "da": "DK",
    "heb": "IL", "he": "IL",
}

# Words in tag display names that imply a country.
_TAG_COUNTRY_KEYWORDS = [
    ("deutsche", "DE"), ("german", "DE"),
    ("french", "FR"), ("français", "FR"),
    ("italian", "IT"), ("italiani", "IT"),
    ("spanish", "ES"), ("español", "ES"),
    ("dutch", "NL"), ("nederlands", "NL"),
    ("polish", "PL"), ("polskie", "PL"),
    ("austrian", "AT"), ("österreich", "AT"),
    ("swiss", "CH"), ("schweiz", "CH"),
    ("british", "GB"), ("uk channels", "GB"),
    ("greek", "GR"),
    ("turkish", "TR"),
    ("russian", "RU"),
    ("ukrainian", "UA"),
]


def _first_name_match(text):
    """Return (cc, weight) of the first matching name pattern, or (None, 0)."""
    if not text:
        return None, 0
    for pat, cc, w in _NAME_PATTERNS:
        if pat.search(text):
            return cc, w
    return None, 0


def _audio_lang_country(channel, services_by_uuid):
    if not services_by_uuid:
        return None
    svc_uuids = channel.get("services") or []
    for su in svc_uuids:
        svc = services_by_uuid.get(su)
        if not svc:
            continue
        # TVHeadend service objects use either "stream" or "streams" depending on version.
        streams = svc.get("stream") or svc.get("streams") or []
        for s in streams:
            # We only care about audio streams; type is e.g. "MPEG2AUDIO", "AAC", "AC3", "EAC3"...
            stype = (s.get("type") or "").upper()
            if "AUDIO" in stype or stype in ("AAC", "AC3", "EAC3", "MP2", "MP3"):
                lang = (s.get("language") or "").lower().strip()
                cc = _LANG_TO_CC.get(lang)
                if cc:
                    return cc
    return None


def guess_country(channel, services_by_uuid=None, tag_names=None, provider=None):
    """Best-effort country code via weighted scoring across multiple signals.

    Signal weights:
        - Trailing/parenthesized country code at end of name: 5
        - First matching name pattern: pattern's weight (default 2)
        - Each matching provider pattern: pattern's weight (default 2)
        - First matching name pattern against tag display strings: same weight
        - Tag keyword (e.g. "deutsche", "polish"): 1
        - Audio stream language → country: 1

    Country with highest total score wins. Returns None when no signal matched.
    """
    scores = {}

    def add(cc, w):
        if cc:
            scores[cc] = scores.get(cc, 0) + w

    name = channel.get("name", "")

    # 1a. Full country name in parens at end (e.g. "Channel 5 (Ukraine)") — very strong.
    m = _PAREN_COUNTRY.search(name)
    if m:
        add(_PAREN_COUNTRY_TO_CC.get(m.group(1).lower()), 5)

    # 1b. Two-letter country code at end (" UK", "(US)") — very strong.
    m = _TRAILING_CC.search(name)
    if m:
        raw = m.group(1).upper()
        add(_TRAILING_CC_FIX.get(raw, raw), 5)

    # 2. First matching name pattern (specific-before-generic order preserved)
    cc, w = _first_name_match(name)
    add(cc, w)

    # 3. Every matching provider pattern — additive
    if provider:
        for pat, pcc, pw in _PROVIDER_PATTERNS:
            if pat.search(provider):
                add(pcc, pw)
    # Also try legacy `provider`/`network` fields on the channel (rarely present)
    for txt in (channel.get("provider"), channel.get("network")):
        if txt:
            for pat, pcc, pw in _PROVIDER_PATTERNS:
                if pat.search(txt):
                    add(pcc, pw)

    # 4. Tag display names: only the keyword scan (broadcaster patterns on tags
    # tend to duplicate provider signals and double-score them).
    if tag_names:
        low = " ".join(t.lower() for t in tag_names if t)
        for needle, t_cc in _TAG_COUNTRY_KEYWORDS:
            if needle in low:
                add(t_cc, 1)

    # 5. Audio stream language → country
    add(_audio_lang_country(channel, services_by_uuid), 1)

    if not scores:
        return None
    # Highest score wins; deterministic tie-break by ISO code (alphabetical).
    return max(scores, key=lambda c: (scores[c], c))
