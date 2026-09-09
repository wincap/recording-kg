import CoreGraphics
import Foundation

let infos = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
for info in infos {
    let owner = info[kCGWindowOwnerName as String] as? String ?? ""
    guard !owner.isEmpty else { continue }
    let name = info[kCGWindowName as String] as? String ?? ""
    let number = info[kCGWindowNumber as String] as? Int ?? 0
    let layer = info[kCGWindowLayer as String] as? Int ?? 0
    guard layer == 0, number > 0 else { continue }
    let bounds = info[kCGWindowBounds as String] as? [String: Any] ?? [:]
    let x = Int((bounds["X"] as? CGFloat) ?? 0)
    let y = Int((bounds["Y"] as? CGFloat) ?? 0)
    let w = Int((bounds["Width"] as? CGFloat) ?? 0)
    let h = Int((bounds["Height"] as? CGFloat) ?? 0)
    if w < 200 || h < 200 { continue }
    print("\(number)\t\(x)\t\(y)\t\(w)\t\(h)\t\(owner)\t\(name)")
}
