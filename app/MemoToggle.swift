import Cocoa

// mem0 MCP 메뉴바 토글
// launchd 단일 인스턴스(com.mem0mcp.server)를 kickstart/kill 로 제어한다.
// (라벨/포트/스크립트명은 install.sh 의 값과 반드시 일치해야 한다.)

let LABEL = "com.mem0mcp.server"
let PORT = 8765
let SERVER_SCRIPT = "mem0_mcp_server.py"   // pgrep 매칭용
let URLSTR = "http://127.0.0.1:8765/mcp"
let PLIST = ("~/Library/LaunchAgents/com.mem0mcp.server.plist" as NSString).expandingTildeInPath
let LOGPATH = ("~/Library/Logs/mem0-mcp.log" as NSString).expandingTildeInPath

@discardableResult
func sh(_ cmd: String) -> String {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/bin/sh")
    p.arguments = ["-c", cmd]
    let pipe = Pipe()
    p.standardOutput = pipe; p.standardError = pipe
    do { try p.run() } catch { return "" }
    p.waitUntilExit()
    return String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
}

func domain() -> String { "gui/\(getuid())/\(LABEL)" }

enum ServerState { case on, starting, off }

func currentState() -> ServerState {
    if sh("/usr/sbin/lsof -nP -iTCP:\(PORT) -sTCP:LISTEN 2>/dev/null").contains("LISTEN") { return .on }
    let pg = sh("/usr/bin/pgrep -f \(SERVER_SCRIPT) 2>/dev/null").trimmingCharacters(in: .whitespacesAndNewlines)
    return pg.isEmpty ? .off : .starting
}

func turnOn()  { sh("/bin/launchctl load -w '\(PLIST)' 2>/dev/null; /bin/launchctl kickstart '\(domain())' 2>/dev/null") }
func turnOff() { sh("/bin/launchctl kill TERM '\(domain())' 2>/dev/null") }

// 커스텀 토글: 완벽한 원형 knob + ON 시 트랙 초록
final class CircleToggle: NSView {
    var isOn = false { didSet { needsDisplay = true } }
    var onChange: ((Bool) -> Void)?
    override var intrinsicContentSize: NSSize { NSSize(width: 48, height: 28) }

    override func draw(_ dirty: NSRect) {
        let h = bounds.height, w = bounds.width
        let pad: CGFloat = 3
        let track = NSBezierPath(roundedRect: bounds, xRadius: h/2, yRadius: h/2)
        (isOn ? NSColor.systemGreen : NSColor(white: 0.42, alpha: 1.0)).setFill()
        track.fill()
        let d = h - 2 * pad
        let x = isOn ? (w - pad - d) : pad
        NSGraphicsContext.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowColor = NSColor.black.withAlphaComponent(0.28)
        shadow.shadowBlurRadius = 1.5
        shadow.shadowOffset = NSSize(width: 0, height: -1)
        shadow.set()
        NSColor.white.setFill()
        NSBezierPath(ovalIn: NSRect(x: x, y: pad, width: d, height: d)).fill()
        NSGraphicsContext.restoreGraphicsState()
    }

    override func mouseDown(with event: NSEvent) {
        isOn.toggle()
        onChange?(isOn)   // 메뉴는 닫지 않음
    }
}

final class SwitchRow: NSView {
    let status = NSTextField(labelWithString: "")
    let toggle = CircleToggle()
    var onChange: ((Bool) -> Void)?

    init() {
        super.init(frame: NSRect(x: 0, y: 0, width: 230, height: 56))
        toggle.frame = NSRect(x: (230 - 48) / 2, y: 26, width: 48, height: 28)
        toggle.onChange = { [weak self] on in self?.onChange?(on) }
        addSubview(toggle)
        status.frame = NSRect(x: 10, y: 7, width: 210, height: 15)
        status.alignment = .center
        status.font = NSFont.systemFont(ofSize: 11, weight: .medium)
        status.textColor = .white
        addSubview(status)
    }
    required init?(coder: NSCoder) { fatalError() }

    func apply(_ state: ServerState) {
        switch state {
        case .on:       toggle.isOn = true;  status.stringValue = URLSTR
        case .starting: toggle.isOn = true;  status.stringValue = "starting…"
        case .off:      toggle.isOn = false; status.stringValue = "OFF"
        }
        status.textColor = .white
    }
}

class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var statusItem: NSStatusItem!
    let menu = NSMenu()
    var timer: Timer?
    var row: SwitchRow?

    func applicationDidFinishLaunching(_ note: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        menu.delegate = self
        statusItem.menu = menu
        updateIcon()
        let t = Timer(timeInterval: 3.0, repeats: true) { [weak self] _ in
            self?.updateIcon()
            self?.row?.apply(currentState())
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    func updateIcon() {
        guard let button = statusItem.button else { return }
        let cfg = NSImage.SymbolConfiguration(pointSize: 15, weight: .regular)
        if let img = NSImage(systemSymbolName: "memorychip", accessibilityDescription: "mem0")?
            .withSymbolConfiguration(cfg) {
            img.isTemplate = true
            button.image = img
            button.imagePosition = .imageOnly
            button.title = ""
            button.contentTintColor = nil
            button.alphaValue = (currentState() == .off) ? 0.45 : 1.0
        } else {
            button.image = nil
            button.title = "M"
        }
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let rowItem = NSMenuItem()
        let r = SwitchRow()
        r.apply(currentState())
        r.onChange = { [weak self] on in
            if on { turnOn() } else { turnOff() }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                self?.row?.apply(currentState())
                self?.updateIcon()
            }
        }
        row = r
        rowItem.view = r
        menu.addItem(rowItem)

        menu.addItem(.separator())
        add(menu, "Refresh", #selector(refreshNow))
        add(menu, "Open log", #selector(openLogs))
        menu.addItem(.separator())
        add(menu, "Quit menu bar app", #selector(quitApp), key: "q")
    }

    func add(_ menu: NSMenu, _ title: String, _ sel: Selector, key: String = "") {
        let it = NSMenuItem(title: title, action: sel, keyEquivalent: key)
        it.target = self
        menu.addItem(it)
    }

    @objc func refreshNow() { updateIcon() }
    @objc func openLogs() { sh("/usr/bin/touch '\(LOGPATH)'; /usr/bin/open -a Console '\(LOGPATH)'") }
    @objc func quitApp() { NSApp.terminate(nil) }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
