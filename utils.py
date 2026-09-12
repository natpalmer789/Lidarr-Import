import unicodedata

def normalize_name(name):
    """
    Normalize names for deciding whether a match is exact
    This intentionally does NOT do fuzzy matching, as it matters that the match is exact.
    Uses casefold and unicodedata.normalize to throughly normalize artist names
    For example:
    name = "ＳＴＲＡẞＥ"

    name = unicodedata.normalize("NFKC", name)
    Result: STRAẞE

    name = name.casefold()
    Result: strasse
    """
    name = unicodedata.normalize("NFKC", name) #Normalize the name
    return " ".join(name.casefold().split()) #Casefold and join to normalize arbitrary whitespace

