def test_fuzzy(target, name):
    target_norm = target.lower().strip()
    name_norm = name.lower().strip()
    
    match = False
    # The buggy logic
    if target_norm and (target_norm in name_norm or name_norm in target_norm): 
        match = True
        print(f"MATCH: '{target}' vs '{name}'")
    else:
        print(f"NO MATCH: '{target}' vs '{name}'")

test_fuzzy("TestUser", "")  # Should be NO MATCH, but is MATCH
test_fuzzy("TestName", "")   # Should be NO MATCH, but is MATCH
