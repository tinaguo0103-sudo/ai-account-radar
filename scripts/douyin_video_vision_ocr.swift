import AppKit
import Foundation
import Vision

struct OCRRow: Codable {
    let path: String
    let text: String
}

var rows: [OCRRow] = []
for path in CommandLine.arguments.dropFirst() {
    autoreleasepool {
        guard let image = NSImage(contentsOfFile: path),
              let data = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: data),
              let cgImage = bitmap.cgImage else {
            rows.append(OCRRow(path: path, text: ""))
            return
        }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = false
        do {
            try VNImageRequestHandler(cgImage: cgImage).perform([request])
            let text = (request.results ?? [])
                .compactMap { $0.topCandidates(1).first?.string }
                .joined(separator: "\n")
            rows.append(OCRRow(path: path, text: text))
        } catch {
            rows.append(OCRRow(path: path, text: ""))
        }
    }
}

let encoded = try JSONEncoder().encode(rows)
FileHandle.standardOutput.write(encoded)
