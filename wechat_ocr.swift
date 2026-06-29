import Foundation
import Vision
import AppKit

// 用法: wechat_ocr <img1> [img2 ...]
// 输出每个文件一段，用定界符包裹，便于 Python 解析:
//   <<<FILE>>>
//   <path>
//   <<<TEXT>>>
//   <识别文字(可能多行)，若为空为 EMPTY>
//   <<<END>>>

func ocrImage(at path: String) -> String {
    guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)) else {
        return "__READ_ERROR__"
    }
    // heic/webp 等由调用方预先转成 png；此处 NSImage 通常能解常见格式
    guard let nsImage = NSImage(data: data),
          let cgImage = nsImage.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        return "__READ_ERROR__"
    }
    var text = ""
    let sem = DispatchSemaphore(value: 0)
    let req = VNRecognizeTextRequest { request, _ in
        let observations = request.results as? [VNRecognizedTextObservation] ?? []
        text = observations.compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
        sem.signal()
    }
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = true
    req.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    DispatchQueue.global(qos: .userInitiated).async {
        do { try handler.perform([req]) } catch { text = "__OCR_ERROR__"; sem.signal() }
    }
    sem.wait()
    return text
}

let paths = CommandLine.arguments.dropFirst()
for path in paths {
    print("<<<FILE>>>")
    print(path)
    print("<<<TEXT>>>")
    let text = ocrImage(at: path)
    print(text.isEmpty ? "EMPTY" : text)
    print("<<<END>>>")
}
