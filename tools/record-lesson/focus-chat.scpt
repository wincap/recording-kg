on walk(elem)
  tell application "System Events"
    try
      if role of elem is "AXTextArea" then
        set {aw, ah} to size of elem
        if aw > 200 then return elem
      end if
    end try
    try
      repeat with c in (UI elements of elem)
        set found to my walk(c)
        if found is not missing value then return found
      end repeat
    end try
  end tell
  return missing value
end walk

tell application "System Events"
  tell process "Cursor"
    set frontmost to true
    set target to my walk(window 1)
    if target is missing value then return "missing"
    set focused of target to true
    click target
    delay 0.1
    set {ax, ay} to position of target
    set {aw, ah} to size of target
    return "focused pos=" & ax & "," & ay & " size=" & aw & "x" & ah
  end tell
end tell
