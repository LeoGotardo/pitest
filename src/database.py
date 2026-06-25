from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Device(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    mac_address = db.Column(db.String(32), nullable=False, unique=True)
    device_id  = db.Column(db.String(64), nullable=True)   # hostname from pitest
    created_at = db.Column(db.DateTime, nullable=False, default=db.func.now())
    updated_at = db.Column(db.DateTime, nullable=False, default=db.func.now(), onupdate=db.func.now())
    tests      = db.relationship("Test", backref="device", lazy=True, order_by="Test.created_at.desc()")


class Test(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    type      = db.Column(db.String(32), nullable=False)
    status    = db.Column(db.String(32), nullable=False)
    message   = db.Column(db.String(256), nullable=True)
    details   = db.Column(db.JSON, nullable=True)
    elapsed_s = db.Column(db.Float, nullable=True)
    device_id = db.Column(db.Integer, db.ForeignKey("device.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=db.func.now())
    updated_at = db.Column(db.DateTime, nullable=False, default=db.func.now(), onupdate=db.func.now())


class Database:
    def commit_info(self, info: dict) -> Exception | None:
        try:
            mac       = info.get("mac_address")
            hostname  = info.get("device_id")
            tests_raw = info.get("tests", {})

            device = Device.query.filter_by(mac_address=mac).first()
            if not device:
                device = Device(mac_address=mac, device_id=hostname)
                db.session.add(device)
                db.session.flush()  # get device.id before adding tests
            else:
                device.device_id = hostname

            for test_type, test_info in tests_raw.items():
                test = Test(
                    device_id = device.id,
                    type      = test_type,
                    status    = test_info.get("status"),
                    message   = test_info.get("message"),
                    details   = test_info.get("details"),
                    elapsed_s = test_info.get("elapsed_s"),
                )
                db.session.add(test)

            db.session.commit()
            return None
        except Exception as e:
            db.session.rollback()
            return e

    def get_all_devices(self) -> list[Device]:
        return Device.query.order_by(Device.updated_at.desc()).all()

    def get_device(self, device_id: int) -> Device | None:
        return Device.query.get(device_id)

    def get_tests(self, device_id: int) -> list[Test]:
        return Test.query.filter_by(device_id=device_id).order_by(Test.created_at.desc()).all()

    def get_latest_overall(self, device_id: int) -> str:
        """Return 'pass', 'fail', or 'none' based on the latest test per type."""
        types = ("lan", "wlan", "boot", "usb", "hotspot")
        statuses = []
        for test_type in types:
            t = (
                Test.query
                .filter_by(device_id=device_id, type=test_type)
                .order_by(Test.created_at.desc())
                .first()
            )
            if t:
                statuses.append(t.status)
        if not statuses:
            return "none"
        return "pass" if all(s == "pass" for s in statuses) else "fail"

    def get_latest_tests(self, device_id: int) -> dict:
        """Return the most recent result for each test type."""
        latest = {}
        for test_type in ("lan", "wlan", "boot", "usb", "hotspot"):
            test = (
                Test.query
                .filter_by(device_id=device_id, type=test_type)
                .order_by(Test.created_at.desc())
                .first()
            )
            if test:
                latest[test_type] = test
        return latest
