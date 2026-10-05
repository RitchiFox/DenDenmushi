import Foundation
import CoreBluetooth

struct NearbyPi: Identifiable {
    let peripheral: CBPeripheral
    var id: UUID { peripheral.identifier }
    let name: String
}

@MainActor
final class BluetoothSetup: NSObject, ObservableObject, @preconcurrency CBCentralManagerDelegate, @preconcurrency CBPeripheralDelegate {
    @Published var nearby: [NearbyPi] = []
    @Published var message = "Натисни «Знайти через Bluetooth»."
    private var central: CBCentralManager?
    private var shouldScan = false
    private var scanID = UUID()
    private var peripheral: CBPeripheral?
    private var characteristics: [String: CBCharacteristic] = [:]
    private var connection: CheckedContinuation<Void, Error>?
    private var readWaiter: CheckedContinuation<Data, Error>?
    private var writeWaiter: CheckedContinuation<Void, Error>?
    private var readID: UUID?
    private var writeID: UUID?
    private var connectID: UUID?
    private var reading: CBUUID?
    private var writing: CBUUID?
    private var challenge = Data()

    func locate(saved: SavedPi?, selected: UUID?) async throws -> NearbyPi? {
        if central == nil { central = CBCentralManager(delegate: self, queue: .main) }
        for _ in 0..<50 {
            if central?.state != .unknown && central?.state != .resetting { break }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        guard let central, central.state == .poweredOn else {
            throw DemoError(central?.state == .unauthorized ? "Дозволь Bluetooth для DenDenMushi у параметрах приватності Mac." : "Увімкни Bluetooth на Mac і натисни «Підключити» ще раз.")
        }
        // CoreBluetooth remembers a stable identifier. Reconnection need not
        // wait for a new advertisement or for a system Settings pairing list.
        if let saved, let peripheral = central.retrievePeripherals(withIdentifiers: [saved.id]).first {
            let pi = NearbyPi(peripheral: peripheral, name: saved.name)
            if !nearby.contains(where: { $0.id == pi.id }) { nearby.append(pi) }
            return pi
        }
        if let selected, let pi = nearby.first(where: { $0.id == selected }) { return pi }
        scan()
        for _ in 0..<75 {
            if let saved, let pi = nearby.first(where: { $0.id == saved.id }) { return pi }
            if saved == nil && !nearby.isEmpty {
                // Brief collection window avoids silently selecting one of two Pi.
                try await Task.sleep(nanoseconds: 1_000_000_000)
                return nearby.count == 1 ? nearby.first : nil
            }
            try await Task.sleep(nanoseconds: 200_000_000)
        }
        throw DemoError("Равлика не знайдено. Увімкни його й піднеси ближче. Якщо це новий пристрій, його власник має один раз підготувати Pi.")
    }

    func scan() {
        scanID = UUID()
        shouldScan = true
        if central == nil { central = CBCentralManager(delegate: self, queue: .main) }
        else { centralManagerDidUpdateState(central!) }
    }
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn:
            guard shouldScan else { return }
            nearby = []; message = "Шукаю равлика поруч…"
            let id = scanID
            central.scanForPeripherals(withServices: [CBUUID(string: SetupWire.service)], options: [CBCentralManagerScanOptionAllowDuplicatesKey: true])
            DispatchQueue.main.asyncAfter(deadline: .now() + 15) { [weak self] in
                guard let self, self.shouldScan, self.scanID == id else { return }
                central.stopScan(); self.shouldScan = false
                self.message = self.nearby.isEmpty ? "Равлика не знайдено. Увімкни Pi. Якщо це нова збірка — спочатку підготуй його нижче." : "Обери свій DenDenMushi."
            }
        case .poweredOff: message = "Увімкни Bluetooth на Mac."
        case .unauthorized: message = "Дозволь Bluetooth для DenDenMushi в Системних параметрах → Приватність і безпека → Bluetooth."
        case .unsupported: message = "Bluetooth Low Energy недоступний на цьому Mac."
        default: message = "Готую Bluetooth…"
        }
    }
    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String: Any], rssi RSSI: NSNumber) {
        guard !nearby.contains(where: { $0.id == peripheral.identifier }) else { return }
        nearby.append(NearbyPi(peripheral: peripheral, name: advertisementData[CBAdvertisementDataLocalNameKey] as? String ?? peripheral.name ?? "DenDenMushi"))
        message = "Равлика знайдено."
    }
    func connect(_ pi: NearbyPi) async throws {
        disconnect()
        guard let central, central.state == .poweredOn else { throw DemoError("Увімкни Bluetooth і повтори пошук.") }
        shouldScan = false; central.stopScan()
        peripheral = pi.peripheral; pi.peripheral.delegate = self
        let id = UUID(); connectID = id
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            connection = continuation
            central.connect(pi.peripheral)
            DispatchQueue.main.asyncAfter(deadline: .now() + 15) { [weak self] in
                if self?.connectID == id { self?.fail(DemoError("Pi не відповідає через Bluetooth. Піднеси його ближче й повтори.")) }
            }
        }
        challenge = try await read(SetupWire.hello)
        guard challenge.count == 16 else { disconnect(); throw DemoError("Pi має іншу версію сервісу. Повтори його підготовку.") }
    }
    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) { peripheral.discoverServices([CBUUID(string: SetupWire.service)]) }
    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) { fail(error ?? DemoError("Bluetooth не підключився.")) }
    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        guard self.peripheral === peripheral else { return }
        fail(error ?? DemoError("Bluetooth-з’єднання завершено."))
    }
    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard error == nil, let service = peripheral.services?.first(where: { $0.uuid == CBUUID(string: SetupWire.service) }) else { fail(error ?? DemoError("На Pi немає сервісу налаштування.")); return }
        peripheral.discoverCharacteristics([SetupWire.hello, SetupWire.request, SetupWire.response].map { CBUUID(string: $0) }, for: service)
    }
    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard error == nil else { fail(error!); return }
        for value in service.characteristics ?? [] { characteristics[value.uuid.uuidString.lowercased()] = value }
        guard [SetupWire.hello, SetupWire.request, SetupWire.response].allSatisfy({ characteristics[$0] != nil }) else { fail(DemoError("Сервіс Bluetooth неповний. Онови Pi через підготовку.")); return }
        connectID = nil; let waiter = connection; connection = nil; waiter?.resume()
    }
    private func read(_ uuid: String) async throws -> Data {
        guard let peripheral, let characteristic = characteristics[uuid], readWaiter == nil else { throw DemoError("Немає з’єднання Bluetooth.") }
        let id = UUID(); readID = id; reading = characteristic.uuid
        return try await withCheckedThrowingContinuation { continuation in
            readWaiter = continuation
            peripheral.readValue(for: characteristic)
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
                if self?.readID == id { self?.fail(DemoError("Pi не відповів. Повтори Bluetooth-підключення.")) }
            }
        }
    }
    func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard self.peripheral === peripheral, reading == characteristic.uuid else { return }
        if let error { fail(error); return }
        readID = nil; reading = nil; let waiter = readWaiter; readWaiter = nil
        waiter?.resume(returning: characteristic.value ?? Data())
    }
    private func write(_ data: Data) async throws {
        guard let peripheral, let characteristic = characteristics[SetupWire.request], writeWaiter == nil else { throw DemoError("Немає з’єднання Bluetooth.") }
        let id = UUID(); writeID = id; writing = characteristic.uuid
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            writeWaiter = continuation
            peripheral.writeValue(data, for: characteristic, type: .withResponse)
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
                if self?.writeID == id { self?.fail(DemoError("Pi не прийняв налаштування. Перевір код равлика й підключись повторно.")) }
            }
        }
    }
    func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic, error: Error?) {
        guard self.peripheral === peripheral, writing == characteristic.uuid else { return }
        if let error { fail(DemoError("Pi відхилив Bluetooth-повідомлення. Перевір код равлика. \(error.localizedDescription)")); return }
        writeID = nil; writing = nil; let waiter = writeWaiter; writeWaiter = nil; waiter?.resume()
    }
    func request(_ values: [String: Any], code: String) async throws -> [String: Any] {
        var body = values; let id = UUID().uuidString; body["id"] = id
        let encrypted = try SetupWire.seal(body, code: code, challenge: challenge)
        let frame = Data((encrypted.base64EncodedString() + "\n").utf8)
        let size = min(180, peripheral?.maximumWriteValueLength(for: .withResponse) ?? 20)
        guard size >= 20 else { throw DemoError("Bluetooth-з’єднання ще не готове.") }
        for start in stride(from: 0, to: frame.count, by: size) { try await write(frame.subdata(in: start..<min(start + size, frame.count))) }
        let deadline = Date().addingTimeInterval(55); var result = Data()
        while Date() < deadline {
            let chunk = try await read(SetupWire.response)
            guard let marker = chunk.first, marker <= 2, result.count + chunk.count <= 8192 else { throw DemoError("Некоректна відповідь Bluetooth.") }
            if marker == 0 { try await Task.sleep(nanoseconds: 200_000_000); continue }
            result.append(chunk.dropFirst())
            if marker == 2 {
                guard let data = Data(base64Encoded: result) else { throw DemoError("Пошкоджена відповідь Bluetooth.") }
                return try SetupWire.open(data, code: code, challenge: challenge, id: id)
            }
        }
        disconnect(); throw DemoError("Налаштування не завершилося вчасно. Перевір мережу й повтори.")
    }
    private func fail(_ error: Error) {
        let c = connection; let r = readWaiter; let w = writeWaiter
        connection = nil; readWaiter = nil; writeWaiter = nil; connectID = nil; readID = nil; writeID = nil; reading = nil; writing = nil
        if let peripheral { self.peripheral = nil; central?.cancelPeripheralConnection(peripheral) }
        characteristics = [:]; challenge = Data()
        c?.resume(throwing: error); r?.resume(throwing: error); w?.resume(throwing: error)
    }
    func disconnect() { fail(DemoError("Підключення скасовано.")) }
}
