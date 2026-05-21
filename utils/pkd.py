PKD_SECTIONS: dict[str, tuple[str, list[int]]] = {
    "A": ("Rolnictwo, leśnictwo, łowiectwo i rybactwo", list(range(1, 4))),
    "B": ("Górnictwo i wydobywanie", list(range(5, 10))),
    "C": ("Przetwórstwo przemysłowe", list(range(10, 34))),
    "D": ("Wytwarzanie i zaopatrywanie w energię elektryczną", [35]),
    "E": ("Dostawa wody; gospodarowanie odpadami i ściekami", list(range(36, 40))),
    "F": ("Budownictwo", list(range(41, 44))),
    "G": ("Handel hurtowy i detaliczny; naprawa pojazdów", list(range(45, 48))),
    "H": ("Transport i gospodarka magazynowa", list(range(49, 54))),
    "I": ("Zakwaterowanie i usługi gastronomiczne", list(range(55, 57))),
    "J": ("Informacja i komunikacja", list(range(58, 64))),
    "K": ("Działalność finansowa i ubezpieczeniowa", list(range(64, 67))),
    "L": ("Obsługa rynku nieruchomości", [68]),
    "M": ("Działalność profesjonalna, naukowa i techniczna", list(range(69, 76))),
    "N": ("Usługi administrowania i działalność wspierająca", list(range(77, 83))),
    "O": ("Administracja publiczna i obrona narodowa", [84]),
    "P": ("Edukacja", [85]),
    "Q": ("Opieka zdrowotna i pomoc społeczna", list(range(86, 89))),
    "R": ("Działalność związana z kulturą, rozrywką i rekreacją", list(range(90, 94))),
    "S": ("Pozostała działalność usługowa", list(range(94, 97))),
    "T": ("Gospodarstwa domowe zatrudniające pracowników", list(range(97, 99))),
    "U": ("Organizacje i zespoły eksterytorialne", [99]),
}

# Płaski słownik: kodDzial (int) → litera sekcji
_DZIAL_TO_SECTION: dict[int, str] = {}
for _letter, (_desc, _dzialy) in PKD_SECTIONS.items():
    for _d in _dzialy:
        _DZIAL_TO_SECTION[_d] = _letter


def get_section(kod_dzial: str | int | None) -> str | None:
    """Zwraca literę sekcji PKD dla podanego kodDzial."""
    if kod_dzial is None:
        return None
    try:
        return _DZIAL_TO_SECTION.get(int(str(kod_dzial).strip()))
    except (ValueError, TypeError):
        return None


def matches_branza(pkd_section: str | None, filter_section: str | None) -> bool:
    """Sprawdza czy sekcja PKD spółki pasuje do filtra. None/'' = wszystkie."""
    if not filter_section:
        return True
    return (pkd_section or "").upper() == filter_section.upper()
