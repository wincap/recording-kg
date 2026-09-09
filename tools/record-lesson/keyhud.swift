import Cocoa
import CoreGraphics

final class Hud: NSObject {
    let panel: NSPanel
    let label: NSTextField
    var tap: CFMachPort?

    override init() {
        let rect = NSRect(x: 0, y: 72, width: 560, height: 56)
        panel = NSPanel(
            contentRect: rect,
            styleMask: [.nonactivatingPanel, .borderless],
            backing: .buffered,
            defer: false
        )
        panel.level = .statusBar
        panel.isOpaque = false
        panel.backgroundColor = NSColor.clear
        panel.ignoresMouseEvents = true
        panel.hasShadow = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isFloatingPanel = true

        let box = NSView(frame: NSRect(x: 0, y: 0, width: 560, height: 56))
        box.wantsLayer = true
        box.layer?.backgroundColor = NSColor(calibratedWhite: 0.08, alpha: 0.72).cgColor
        box.layer?.cornerRadius = 16

        label = NSTextField(labelWithString: "")
        label.frame = NSRect(x: 16, y: 10, width: 528, height: 36)
        label.alignment = .center
        label.font = NSFont.monospacedSystemFont(ofSize: 22, weight: .medium)
        label.textColor = .white
        box.addSubview(label)
        panel.contentView = box
        super.init()
        recenter()
        panel.orderFrontRegardless()
        installTap()
    }

    func recenter() {
        guard let screen = NSScreen.main?.visibleFrame else { return }
        let x = screen.midX - panel.frame.width / 2
        let y = screen.minY + 28
        panel.setFrameOrigin(NSPoint(x: x, y: y))
    }

    func show(_ text: String) {
        label.stringValue = text
        panel.alphaValue = 1
        NSObject.cancelPreviousPerformRequests(withTarget: self)
        perform(#selector(fade), with: nil, afterDelay: 1.1)
    }

    @objc func fade() {
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.2
            panel.animator().alphaValue = 0
        }
    }

    func installTap() {
        let mask = (1 << CGEventType.keyDown.rawValue) | (1 << CGEventType.leftMouseDown.rawValue)
        let callback: CGEventTapCallBack = { _, type, event, refcon in
            guard let refcon else { return Unmanaged.passUnretained(event) }
            let hud = Unmanaged<Hud>.fromOpaque(refcon).takeUnretainedValue()
            if type == .keyDown {
                hud.show(Hud.describe(event))
            } else if type == .leftMouseDown {
                hud.show("CLICK")
            }
            return Unmanaged.passUnretained(event)
        }
        let refcon = Unmanaged.passUnretained(self).toOpaque()
        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .listenOnly,
            eventsOfInterest: CGEventMask(mask),
            callback: callback,
            userInfo: refcon
        ) else {
            fputs("keyhud: 需要辅助功能权限才能显示按键\n", stderr)
            return
        }
        self.tap = tap
        let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), source, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
    }

    static func describe(_ event: CGEvent) -> String {
        let key = UInt16(event.getIntegerValueField(.keyboardEventKeycode))
        var parts: [String] = []
        let flags = event.flags
        if flags.contains(.maskControl) { parts.append("⌃") }
        if flags.contains(.maskAlternate) { parts.append("⌥") }
        if flags.contains(.maskShift) { parts.append("⇧") }
        if flags.contains(.maskCommand) { parts.append("⌘") }
        parts.append(names[key] ?? "·")
        return parts.joined()
    }
}

let names: [UInt16: String] = [
    0: "A", 1: "S", 2: "D", 3: "F", 4: "H", 5: "G", 6: "Z", 7: "X",
    8: "C", 9: "V", 11: "B", 12: "Q", 13: "W", 14: "E", 15: "R",
    16: "Y", 17: "T", 31: "O", 32: "U", 34: "I", 35: "P", 37: "L",
    38: "J", 40: "K", 45: "N", 46: "M",
    18: "1", 19: "2", 20: "3", 21: "4", 22: "6", 23: "5", 25: "9",
    26: "7", 28: "8", 29: "0",
    36: "⏎", 48: "⇥", 49: "Space", 51: "⌫", 53: "Esc",
    123: "←", 124: "→", 125: "↓", 126: "↑",
]

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
_ = Hud()
app.run()
