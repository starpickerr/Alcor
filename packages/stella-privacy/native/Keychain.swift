import Foundation
import Security

struct Request: Decodable {
    let service: String
    let operation: String
    let value: String?
    let keychainPath: String?
}

struct Reply: Encodable {
    let ok: Bool
    var value: String? = nil
    var status: Int32? = nil
}

struct KeychainFailure: Error {
    let status: OSStatus
}

func check(_ status: OSStatus) throws {
    if status != errSecSuccess { throw KeychainFailure(status: status) }
}

func perform(_ request: Request) throws -> Reply {
    guard request.service.hasPrefix("com.stella.dsh.credentials.v1."),
          request.service.count == 94,
          ["read", "write"].contains(request.operation) else {
        throw KeychainFailure(status: errSecParam)
    }
    try check(SecKeychainSetUserInteractionAllowed(false))
    var keychain: SecKeychain?
    if let path = request.keychainPath {
        try check(SecKeychainOpen(path, &keychain))
    } else {
        try check(SecKeychainCopyDefault(&keychain))
    }
    guard let keychain else { throw KeychainFailure(status: errSecNoDefaultKeychain) }
    var state: SecKeychainStatus = 0
    try check(SecKeychainGetStatus(keychain, &state))
    guard state & SecKeychainStatus(kSecUnlockStateStatus) != 0 else {
        throw KeychainFailure(status: errSecInteractionNotAllowed)
    }
    let attributes: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: request.service,
        kSecAttrAccount as String: "dsh",
        kSecUseAuthenticationUI as String: kSecUseAuthenticationUIFail,
    ]
    var query = attributes
    query[kSecMatchSearchList as String] = [keychain]
    if request.operation == "read" {
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return Reply(ok: true) }
        try check(status)
        guard let data = result as? Data, data.count <= 1_048_576,
              let value = String(data: data, encoding: .utf8) else {
            throw KeychainFailure(status: errSecDecode)
        }
        return Reply(ok: true, value: value)
    }
    guard let value = request.value, let data = value.data(using: .utf8), data.count <= 1_048_576 else {
        throw KeychainFailure(status: errSecParam)
    }
    let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
    if status == errSecItemNotFound {
        var addition = attributes
        addition[kSecUseKeychain as String] = keychain
        addition[kSecValueData as String] = data
        try check(SecItemAdd(addition as CFDictionary, nil))
    } else {
        try check(status)
    }
    return Reply(ok: true)
}

let reply: Reply
do {
    let input = FileHandle.standardInput.readData(ofLength: 2_097_153)
    guard input.count <= 2_097_152 else { throw KeychainFailure(status: errSecParam) }
    reply = try perform(JSONDecoder().decode(Request.self, from: input))
} catch let failure as KeychainFailure {
    reply = Reply(ok: false, status: failure.status)
} catch {
    reply = Reply(ok: false, status: errSecParam)
}
FileHandle.standardOutput.write(try JSONEncoder().encode(reply))
