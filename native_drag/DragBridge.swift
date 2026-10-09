// SPDX-License-Identifier: GPL-3.0-only
// AppKit file-URL drag source. Media bytes never cross this process's pipes.
import AppKit
import Foundation

func reply(_ value: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else { return }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([10]))
}

final class DragView: NSView, NSDraggingSource {
    var url: URL?
    var identifier = ""
    var direct = false
    var busy = false
    var completion: (() -> Void)?

    override func draw(_ dirtyRect: NSRect) {
        guard !direct, let url = url else { return }
        NSColor.windowBackgroundColor.setFill()
        NSBezierPath(rect: bounds).fill()
        let text = url.lastPathComponent + "\nDrag this file into an application. Esc closes this window."
        (text as NSString).draw(in: bounds.insetBy(dx: 14, dy: 12), withAttributes: [
            .font: NSFont.systemFont(ofSize: 13), .foregroundColor: NSColor.labelColor])
    }

    override var acceptsFirstResponder: Bool { true }
    override func keyDown(with event: NSEvent) {
        if event.keyCode == 53 && !busy { window?.close(); completion?() }
        else { super.keyDown(with: event) }
    }
    override func mouseDown(with event: NSEvent) { }
    override func mouseDragged(with event: NSEvent) {
        if !busy { begin(event: event) }
    }

    func begin(event: NSEvent) {
        guard let url = url, !busy else { return }
        busy = true
        let item = NSDraggingItem(pasteboardWriter: url as NSURL)
        let image = NSWorkspace.shared.icon(forFile: url.path)
        image.size = NSSize(width: 32, height: 32)
        let point = convert(event.locationInWindow, from: nil)
        item.setDraggingFrame(NSRect(origin: point, size: image.size), contents: image)
        let session = beginDraggingSession(with: [item], event: event, source: self)
        session.animatesToStartingPositionsOnCancelOrFail = false
    }

    func draggingSession(_ session: NSDraggingSession, sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation {
        return .copy
    }
    func ignoreModifierKeys(for session: NSDraggingSession) -> Bool { true }
    func draggingSession(_ session: NSDraggingSession, endedAt screenPoint: NSPoint, operation: NSDragOperation) {
        busy = false
        reply(["id": identifier, "ok": true, "phase": "finished", "effect": operation == .none ? "None" : "Copy"])
        window?.orderOut(nil)
        completion?()
    }
}

final class ProbeView: NSView {
    var report: URL!
    var label: NSTextField!
    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation { .copy }
    override func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool { true }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        let urls = sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: [
            .urlReadingFileURLsOnly: true]) as? [URL] ?? []
        guard !urls.isEmpty else { return false }
        let paths = urls.map { $0.path }
        label.stringValue = paths.joined(separator: "\n")
        let value: [String: Any] = ["paths": paths, "formats": sender.draggingPasteboard.types?.map { $0.rawValue } ?? [],
                                    "time": Date().timeIntervalSince1970]
        if let data = try? JSONSerialization.data(withJSONObject: value) {
            if !FileManager.default.fileExists(atPath: report.path) {
                FileManager.default.createFile(atPath: report.path, contents: nil)
            }
            if let handle = try? FileHandle(forWritingTo: report) {
                handle.seekToEndOfFile(); handle.write(data); handle.write(Data([10])); handle.closeFile()
            }
        }
        return true
    }
}

final class Bridge: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var windows: [NSWindow] = []
    var source: DragView?

    func windowWillClose(_ notification: Notification) {
        guard let window = notification.object as? NSWindow else { return }
        if source?.window === window { source = nil }
        windows.removeAll { $0 === window }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        DispatchQueue.global(qos: .userInitiated).async {
            while let line = readLine() {
                guard line.utf8.count <= 65536,
                      let data = line.data(using: .utf8),
                      let command = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { continue }
                DispatchQueue.main.async { self.command(command) }
            }
            DispatchQueue.main.async { NSApp.terminate(nil) }
        }
    }

    func command(_ command: [String: Any]) {
        let id = command["id"] as? String ?? ""
        let action = command["action"] as? String ?? ""
        let path = command["path"] as? String ?? ""
        if action == "ping" { reply(["id": id, "ok": true, "ready": true]); return }
        if action == "choose_folder" {
            let panel = NSOpenPanel()
            panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
            panel.canCreateDirectories = true; panel.message = "Select an AIRenamer project folder"
            if !path.isEmpty { panel.directoryURL = URL(fileURLWithPath: path) }
            NSApp.activate(ignoringOtherApps: true)
            let selected = panel.runModal() == .OK ? panel.url?.path ?? "" : ""
            reply(["id": id, "ok": true, "path": selected]); return
        }
        if action == "probe" {
            let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 580, height: 220),
                                styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
            panel.title = "AIRenamer — Drop test target"
            let view = ProbeView(frame: NSRect(x: 0, y: 0, width: 580, height: 220))
            view.autoresizingMask = [.width, .height]
            view.report = URL(fileURLWithPath: path)
            view.registerForDraggedTypes([.fileURL])
            let label = NSTextField(wrappingLabelWithString: "Drop a saved file here. The original path must appear below.")
            label.frame = view.bounds.insetBy(dx: 14, dy: 14); label.autoresizingMask = [.width, .height]
            view.label = label; view.addSubview(label); panel.contentView = view
            panel.isReleasedWhenClosed = false; panel.delegate = self; panel.center(); windows.append(panel)
            panel.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
            reply(["id": id, "ok": true, "opened": true]); return
        }
        let url = URL(fileURLWithPath: path)
        guard path.hasPrefix("/"), FileManager.default.fileExists(atPath: path) else {
            reply(["id": id, "ok": false, "error": "The source file no longer exists."]); return
        }
        if action == "inspect" {
            let board = NSPasteboard(name: NSPasteboard.Name(UUID().uuidString))
            board.writeObjects([url as NSURL])
            let urls = board.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL] ?? []
            reply(["id": id, "ok": true, "paths": urls.map { $0.path }, "nativePaths": urls.map { $0.path }, "format": "public.file-url"])
            board.releaseGlobally(); return
        }
        guard action == "begin" || action == "handle" else {
            reply(["id": id, "ok": false, "error": "Unknown drag helper action."]); return
        }
        guard source == nil else {
            reply(["id": id, "ok": false, "error": "A native drag is already active."]); return
        }
        let direct = action == "begin"
        if direct {
            let expires = (command["expires"] as? NSNumber)?.doubleValue ?? 0
            guard expires >= Date().timeIntervalSince1970 * 1000 else {
                reply(["id": id, "ok": false, "error": "Native drag request expired."]); return
            }
            guard NSEvent.pressedMouseButtons & 1 != 0 else {
                reply(["id": id, "ok": false, "error": "The mouse button was released. Hold it while dragging."]); return
            }
        }
        let mouse = NSEvent.mouseLocation
        let rect = NSRect(x: mouse.x - 16, y: mouse.y - 16, width: direct ? 32 : 430, height: direct ? 32 : 86)
        let panel = NSPanel(contentRect: rect, styleMask: direct ? [.borderless, .nonactivatingPanel] : [.titled, .closable],
                            backing: .buffered, defer: false)
        panel.title = "AIRenamer — Drag to app"
        panel.level = .floating; panel.hidesOnDeactivate = false; panel.isReleasedWhenClosed = false
        panel.delegate = self
        if direct { panel.isOpaque = false; panel.backgroundColor = .clear; panel.hasShadow = false }
        let view = DragView(frame: NSRect(origin: .zero, size: rect.size))
        view.url = url; view.identifier = id; view.direct = direct
        view.completion = { [weak self, weak panel] in
            panel?.close(); self?.source = nil
            self?.windows.removeAll { $0 === panel }
        }
        panel.contentView = view; source = view; windows.append(panel)
        panel.orderFrontRegardless()
        if !direct { panel.makeKeyAndOrderFront(nil); panel.makeFirstResponder(view); NSApp.activate(ignoringOtherApps: true) }
        if direct {
            // No injected global input: AppKit takes over the held gesture from a native source view.
            let location = panel.convertPoint(fromScreen: mouse)
            guard let event = NSEvent.mouseEvent(with: .leftMouseDragged, location: location, modifierFlags: [],
                timestamp: ProcessInfo.processInfo.systemUptime, windowNumber: panel.windowNumber,
                context: nil, eventNumber: 0, clickCount: 1, pressure: 1) else {
                panel.close(); source = nil; windows.removeAll { $0 === panel }
                reply(["id": id, "ok": false, "error": "Could not start the native drag gesture."]); return
            }
            // Acknowledge before AppKit enters mouse tracking; the Python host
            // can then serve completion/status requests during a long gesture.
            reply(["id": id, "ok": true, "opened": false, "phase": "started"])
            view.begin(event: event)
            return
        }
        reply(["id": id, "ok": true, "opened": true, "phase": "ready"])
    }
}

let app = NSApplication.shared
let bridge = Bridge()
app.setActivationPolicy(.accessory)
app.delegate = bridge
app.run()
