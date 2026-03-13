import re

regex = r"\b(?:[a-zA-Z0-9.-]+\s+){1,4}at\s+(?:[a-zA-Z0-9.-]+\s*){1,4}(?:dot|com|net|org|edu)\b"

strings = [
    "Or my email address is also at hotmail.com.",
    "Yes my name is John and my email is john at gmail dot com.",
    "a b c at d e f dot org",
    "the quick brown fox at a.com",
    "amber sudduth at hotmail.com",
    "j o h n at a b c dot com"
]

for s in strings:
    print(f"'{s}' -> {re.findall(regex, s)}")
