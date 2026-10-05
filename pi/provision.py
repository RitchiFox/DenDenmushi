#!/usr/bin/python3
"""BlueZ GATT service for DenDenMushi. Separate from existing audio profiles."""
import json
import sys
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from provision_core import Protocol, SERVICE, CHALLENGE, REQUEST, RESPONSE
from provision_backend import Backend

PROPS = "org.freedesktop.DBus.Properties"
MANAGED = "org.freedesktop.DBus.ObjectManager"
SERVICE_IF = "org.bluez.GattService1"
CHAR_IF = "org.bluez.GattCharacteristic1"
AD_IF = "org.bluez.LEAdvertisement1"
BASE = "/dev/denden/setup"


class Failed(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.Failed"


class Object(dbus.service.Object):
    def __init__(self, bus, path, interface, properties):
        super().__init__(bus, path)
        self.path, self.interface, self.properties = path, interface, properties

    @dbus.service.method(PROPS, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        return self.properties if interface == self.interface else {}

    @dbus.service.method(PROPS, in_signature="ss", out_signature="v")
    def Get(self, interface, key):
        if interface != self.interface or key not in self.properties:
            raise Failed("Unknown property")
        return self.properties[key]


class Characteristic(Object):
    def __init__(self, bus, index, uuid, flags, protocol):
        super().__init__(bus, BASE + "/service/char" + str(index), CHAR_IF,
                         {"UUID": uuid, "Service": dbus.ObjectPath(BASE + "/service"), "Flags": dbus.Array(flags, signature="s")})
        self.uuid, self.protocol = uuid, protocol

    @dbus.service.method(CHAR_IF, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        try:
            if int(options.get("offset", 0)) != 0:
                raise ValueError()
            peer = str(options["device"])
            data = self.protocol.hello(peer) if self.uuid == CHALLENGE else self.protocol.read(peer) if self.uuid == RESPONSE else None
            if data is None:
                raise ValueError()
            return dbus.ByteArray(data)
        except Exception:
            raise Failed("Setup session unavailable; reconnect and check setup code")

    @dbus.service.method(CHAR_IF, in_signature="aya{sv}", out_signature="")
    def WriteValue(self, value, options):
        try:
            if self.uuid != REQUEST or int(options.get("offset", 0)) != 0:
                raise ValueError()
            self.protocol.write(str(options["device"]), bytes(value))
        except Exception:
            raise Failed("Setup message rejected; check setup code and reconnect")


class Application(dbus.service.Object):
    def __init__(self, bus, protocol):
        super().__init__(bus, BASE)
        self.objects = [Object(bus, BASE + "/service", SERVICE_IF, {"UUID": SERVICE, "Primary": dbus.Boolean(True)})]
        self.objects.extend(Characteristic(bus, i, uuid, flags, protocol) for i, (uuid, flags) in enumerate([(CHALLENGE, ["read"]), (REQUEST, ["write"]), (RESPONSE, ["read"])]))

    @dbus.service.method(MANAGED, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        return {obj.path: {obj.interface: obj.properties} for obj in self.objects}


class Advertisement(Object):
    def __init__(self, bus):
        super().__init__(bus, BASE + "_advertisement", AD_IF,
                         {"Type": "peripheral", "ServiceUUIDs": dbus.Array([SERVICE], signature="s"), "LocalName": "DenDenMushi"})

    @dbus.service.method(AD_IF, in_signature="", out_signature="")
    def Release(self):
        GLib.idle_add(lambda: sys.exit(1))


def main():
    with open("/etc/denden-demo/provision.json") as f:
        config = json.load(f)
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    protocol = Protocol(config["code"], Backend(config["user"]))
    app = Application(bus, protocol)
    advertisement = Advertisement(bus)
    objects = dbus.Interface(bus.get_object("org.bluez", "/"), MANAGED).GetManagedObjects()
    adapter = next((p for p, interfaces in objects.items() if "org.bluez.GattManager1" in interfaces and "org.bluez.LEAdvertisingManager1" in interfaces), None)
    if adapter is None:
        raise RuntimeError("Bluetooth adapter lacks GATT/advertising support")
    proxy = bus.get_object("org.bluez", adapter)
    dbus.Interface(proxy, PROPS).Set("org.bluez.Adapter1", "Powered", dbus.Boolean(True))
    from pairing import start_pairing
    try:
        pairing_state = start_pairing(bus, adapter, config["user"])
    except Exception:
        # Classic pairing is optional; keep authenticated BLE provisioning alive.
        print("Classic Bluetooth pairing unavailable", flush=True)
        pairing_state = None
    loop = GLib.MainLoop()

    def failed(error):
        print("Bluetooth setup registration failed: " + error.get_dbus_name(), flush=True)
        loop.quit()

    def registered():
        dbus.Interface(proxy, "org.bluez.LEAdvertisingManager1").RegisterAdvertisement(
            advertisement.path, {}, reply_handler=lambda: print("Bluetooth setup ready", flush=True), error_handler=failed)

    def changed(interface, values, invalidated, path=None):
        if interface == "org.bluez.Device1" and "Connected" in values and not bool(values["Connected"]):
            protocol.drop(str(path))

    bus.add_signal_receiver(changed, dbus_interface=PROPS, signal_name="PropertiesChanged", path_keyword="path", bus_name="org.bluez")
    # Async registration is essential: BlueZ calls back into our ObjectManager.
    dbus.Interface(proxy, "org.bluez.GattManager1").RegisterApplication(BASE, {}, reply_handler=registered, error_handler=failed)
    loop.run()
    return 1


if __name__ == "__main__":
    sys.exit(main())
