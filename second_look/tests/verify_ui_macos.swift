// Optional native WebKit QA for macOS. Start the local server first.
// swiftc tests/verify_ui_macos.swift -o /tmp/second-look-ui-qa
// /tmp/second-look-ui-qa http://127.0.0.1:8000 dist/ui-validation
import Foundation
import AppKit
import WebKit

struct QAError: Error, CustomStringConvertible { let description:String }
@MainActor final class Harness:NSObject, WKNavigationDelegate, WKScriptMessageHandler {
    let web:WKWebView
    let window:NSWindow
    let output:URL
    let base:String
    var failures:[String]=[]
    var navigationCount=0
    func webView(_ webView:WKWebView,didCommit navigation:WKNavigation!) { navigationCount += 1 }
    init(base:String,output:String,width:Double,height:Double) {
        self.base=base; self.output=URL(fileURLWithPath:output)
        let config=WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        web=WKWebView(frame:NSRect(x:0,y:0,width:width,height:height),configuration:config)
        window=NSWindow(contentRect:NSRect(x:-5000,y:0,width:width,height:height),styleMask:[.borderless],backing:.buffered,defer:false)
        super.init()
        config.userContentController.add(self,name:"qa")
        config.userContentController.addUserScript(WKUserScript(source:"window.addEventListener('error',e=>window.webkit.messageHandlers.qa.postMessage('JS ERROR: '+e.message));",injectionTime:.atDocumentStart,forMainFrameOnly:true))
        web.navigationDelegate=self
        web.autoresizingMask=[.width,.height]
        window.contentView=web
        window.orderBack(nil)
    }
    func userContentController(_ userContentController:WKUserContentController,didReceive message:WKScriptMessage) { print(message.body);fflush(stdout) }
    func js(_ source:String) async throws -> Any {
        try await withCheckedThrowingContinuation { continuation in
            web.evaluateJavaScript(source) { result,error in
                if let error=error {continuation.resume(throwing:error)} else {continuation.resume(returning:result ?? NSNull())}
            }
        }
    }
    func act(_ source:String) async throws { _=try await js("(()=>{\(source);return true})()") }
    func wait(_ condition:String) async throws {
        for _ in 0..<150 {
            if let value=try await js(condition) as? Bool,value {return}
            try await Task.sleep(nanoseconds:100_000_000)
        }
        throw QAError(description:"Timed out: "+condition)
    }
    func check(_ condition:String,_ label:String) async throws {
        guard let value=try await js(condition) as? Bool,value else {throw QAError(description:"FAILED: "+label)}
        print("PASS: "+label);fflush(stdout)
    }
    func screenshot(_ name:String) async throws {
        try FileManager.default.createDirectory(at:output,withIntermediateDirectories:true)
        web.layoutSubtreeIfNeeded()
        try await Task.sleep(nanoseconds:150_000_000) // allow the committed WebKit frame to reach the snapshot compositor
        let configuration=WKSnapshotConfiguration();configuration.rect=web.bounds
        let result:NSImage=try await withCheckedThrowingContinuation { continuation in
            web.takeSnapshot(with:configuration) { image,error in
                if let error=error {continuation.resume(throwing:error)} else if let image=image {continuation.resume(returning:image)} else {continuation.resume(throwing:QAError(description:"Missing screenshot"))}
            }
        }
        let bitmap=NSBitmapImageRep(data:result.tiffRepresentation!)!
        try bitmap.representation(using:.png,properties:[:])!.write(to:output.appendingPathComponent(name+".png"))
        print("SCREENSHOT: "+output.appendingPathComponent(name+".png").path);fflush(stdout)
    }
    func navigate(_ path:String="/") async throws {
        let before=navigationCount
        web.load(URLRequest(url:URL(string:base+path)!))
        for _ in 0..<150 {
            if navigationCount>before {break}
            try await Task.sleep(nanoseconds:100_000_000)
        }
        guard navigationCount>before else {throw QAError(description:"Navigation did not commit")}
        try await wait("document.querySelector('#market-search') && !document.querySelector('#market-search').disabled")
    }
    func scenario(_ index:Int) async throws {
        try await act("document.querySelector('#scenario-select').value='\(index)';document.querySelector('#scenario-select').dispatchEvent(new Event('change',{bubbles:true}))")
        try await wait("document.querySelector('#trade-dialog').open")
    }
    func run() async {
        do {
            try await navigate()
            try await check("document.body.scrollWidth<=innerWidth", "desktop has no page overflow")
            try await check("document.querySelector('#agent-invite').hidden && document.querySelector('#review-panel').hidden", "no agent invitation on initial page")
            try await screenshot("desktop-market")
            try await act("document.querySelector('.trade-actions [data-open-side=buy]').focus();document.querySelector('.trade-actions [data-open-side=buy]').click()")
            try await wait("document.querySelector('#trade-dialog').open && document.activeElement.id==='amount'")
            try await act("document.querySelector('#market-search').focus()")
            try await check("document.activeElement.closest('#trade-dialog')!==null", "native modal focus cannot escape into the underlying page")
            try await screenshot("desktop-ticket")
            try await act("window.__calls=[];window.__realFetch=window.fetch;window.fetch=(url,options)=>{window.__calls.push({url:String(url),body:options?.body});return window.__realFetch(url,options)}")
            try await act("document.querySelector('#close-ticket').click()")
            try await check("!document.querySelector('#trade-dialog').open && window.__calls.length===0", "closing ticket does not submit or approve a trade")
            try await check("document.activeElement.matches('.trade-actions [data-open-side=buy]')", "closing ticket restores keyboard focus to its opener")
            try await scenario(0)
            try await act("document.querySelector('#submit-trade').click()")
            try await wait("document.querySelector('#ticket-status').classList.contains('green')")
            try await check("document.querySelector('#agent-invite').hidden && document.querySelector('#review-panel').hidden", "green has no character or conversation")
            try await act("document.querySelector('#close-ticket').click();document.querySelector('#client-select').value='mateo';document.querySelector('#client-select').dispatchEvent(new Event('change',{bubbles:true}))")
            try await wait("document.querySelector('#account-name').textContent==='Mateo'")
            try await scenario(0)
            try await act("document.querySelector('#submit-trade').click()")
            try await wait("document.querySelector('#ticket-status').classList.contains('yellow')")
            try await check("document.querySelector('#agent-invite').hidden && document.querySelector('#review-panel').hidden", "yellow has no character or agent invitation")
            try await act("document.querySelector('#confirm-yellow').click()")
            try await wait("document.querySelector('#ticket-status').textContent.includes('Simulation continued')")
            try await act("document.querySelector('#close-ticket').click();document.querySelector('#client-select').value='robert';document.querySelector('#client-select').dispatchEvent(new Event('change',{bubbles:true}))")
            try await wait("document.querySelector('#account-name').textContent==='Robert'")
            try await scenario(1)
            try await act("document.querySelector('#submit-trade').click()")
            try await wait("!document.querySelector('#agent-invite').hidden")
            try await check("document.querySelector('#ticket-status').classList.contains('red') && document.querySelector('#amount').value==='360000'", "red pauses and preserves the pending order")
            try await check("document.querySelector('#invite-title').textContent==='Hi, Robert!'", "invitation uses the actual fictional first name")
            try await screenshot("desktop-red-invitation")
            try await act("document.querySelector('#lets-talk').click()")
            try await check("!document.querySelector('#review-panel').hidden && document.querySelector('#agent-invite').hidden && document.activeElement.id==='reason'", "Let's talk expands explanation and focuses conversation")
            try await screenshot("desktop-red-review")
            try await act("document.querySelector('#amount').value='350000';document.querySelector('#amount').dispatchEvent(new Event('input',{bubbles:true}))")
            try await check("document.querySelector('#review-panel').hidden && document.querySelector('#agent-invite').hidden", "editing immediately invalidates the previous red result")
            try await wait("!document.querySelector('#agent-invite').hidden")
            try await check("window.__calls.filter(c=>c.url==='/api/evaluate').map(c=>JSON.parse(c.body)).at(-1).amount_cad===350000", "edited amount triggers a fresh evaluation")
            try await act("document.querySelector('#dismiss-invite').click()")
            try await check("!document.querySelector('#review-panel').hidden && document.querySelector('#agent-invite').hidden && document.activeElement.id!=='reason'", "invitation close reveals the quiet explanation without focusing input")
            try await act("window.__beforeClose=window.__calls.length;document.querySelector('#close-ticket').click()")
            try await check("!document.querySelector('#trade-dialog').open && document.querySelector('#page-review-slot').contains(document.querySelector('#review-panel')) && window.__calls.length===window.__beforeClose", "ticket close preserves a quiet pending review and performs no server decision")
            try await act("document.querySelector('#revise-trade').click();document.querySelector('#amount').value='340000';document.querySelector('#amount').dispatchEvent(new Event('input',{bubbles:true}))")
            try await wait("!document.querySelector('#agent-invite').hidden")
            try await act("document.querySelector('#lets-talk').click();document.querySelector('#reason').value='I am interested in long-term growth.';document.querySelector('#reason-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))")
            try await wait("document.querySelector('#conversation-result').textContent.includes('unavailable in this local demo')")
            try await check("document.querySelector('#conversation-result').textContent.includes('I am interested in long-term growth.')", "investor response is saved and unavailable AI is explicit")
            try await check("document.querySelector('#proceed-red').disabled", "red cannot proceed before acknowledgement")
            try await act("document.querySelector('#risk-checkbox').checked=true;document.querySelector('#risk-checkbox').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#proceed-red').click()")
            try await wait("!document.querySelector('#review-terminal').hidden")
            try await check("document.querySelector('#review-terminal').textContent.includes('verdict remains red')", "risk acknowledgement continues only the simulation and retains red")
            try await act("document.querySelector('#close-ticket').click()")
            // Mock one request failure, with the real service restored afterwards.
            try await scenario(0)
            try await act("window.fetch=(url,options)=>String(url)==='/api/evaluate'?Promise.reject(new Error('Test service unavailable')):window.__realFetch(url,options);document.querySelector('#submit-trade').click()")
            try await wait("!document.querySelector('#ticket-error').hidden")
            try await check("!document.querySelector('#submit-trade').disabled && document.querySelector('#agent-invite').hidden && document.querySelector('#review-panel').hidden", "failed evaluation is retryable and never shows stale approval or agent UI")
            try await act("window.fetch=window.__realFetch;document.querySelector('#close-ticket').click()")
            // Pending requests are held briefly; duplicate submits must not issue another evaluation.
            try await scenario(0)
            try await act("window.__pendingCount=0;window.fetch=(url,options)=>{if(String(url)!=='/api/evaluate')return window.__realFetch(url,options);window.__pendingCount++;return new Promise((resolve,reject)=>setTimeout(()=>window.__realFetch(url,options).then(resolve,reject),600))};document.querySelector('#trade-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));document.querySelector('#trade-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))")
            try await check("document.querySelector('#submit-trade').disabled && window.__pendingCount===1", "pending evaluation prevents duplicate submissions")
            try await wait("document.querySelector('#ticket-status').classList.contains('green')")
            try await act("window.fetch=window.__realFetch;document.querySelector('#close-ticket').click()")
            // Mobile checks use the real app and backend.
            window.setContentSize(NSSize(width:390,height:844));web.setFrameSize(NSSize(width:390,height:844))
            try await navigate()
            try await check("innerWidth===390 && document.body.scrollWidth<=innerWidth", "mobile main screen fits the viewport")
            try await screenshot("mobile-market")
            try await act("document.querySelector('.trade-actions [data-open-side=buy]').click()")
            try await wait("document.querySelector('#trade-dialog').open")
            try await check("document.querySelector('.ticket-quote').getBoundingClientRect().top>=document.querySelector('#trade-form').getBoundingClientRect().bottom", "mobile trade form and quote stack vertically")
            try await check("document.querySelector('#trade-dialog').scrollWidth<=document.querySelector('#trade-dialog').clientWidth+2", "mobile trade ticket has no horizontal overflow")
            try await screenshot("mobile-ticket")
            try await act("window.__beforeEscape=window.__calls?.length||0;document.querySelector('#trade-dialog').dispatchEvent(new Event('cancel',{cancelable:true}))")
            try await check("!document.querySelector('#trade-dialog').open", "Escape dismisses the modal without approving a trade")
            print("ALL UI CHECKS PASSED");fflush(stdout)
            exit(0)
        } catch {try? await screenshot("failure"); print("UI QA FAILED: \(error)");fflush(stdout);exit(1)}
    }
}
let args=CommandLine.arguments
let base=args.count>1 ? args[1] : "http://127.0.0.1:8000"
let output=args.count>2 ? args[2] : "/private/tmp/second-look-ui-screens"
let app=NSApplication.shared
app.setActivationPolicy(.accessory)
Task { @MainActor in
    let runner=Harness(base:base,output:output,width:1280,height:900)
    await runner.run()
}
app.run()
