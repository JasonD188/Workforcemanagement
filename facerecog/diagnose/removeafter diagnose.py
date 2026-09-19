"""
Standalone diagnostic script - hindi ito bahagi ng Flask app, tumatakbo
mismo sa parehong environment/imports ng buong proyekto (kaya kailangang
patakbuhin mula sa root folder ng proyekto, kung saan makikita ang mga
module tulad ng `database`, `config`, `api`, atbp.)

PAANO GAMITIN:
    python diagnose_employee.py f9a53db4

Isasauli nito ang end-to-end na resulta: mula sa Postgres record, hanggang
sa Appwrite Storage file mismo, hanggang sa aktwal na face encoding, at
kung nasa KASALUKUYANG in-memory na listahan ito para sa scanning.
"""

import sys
import json


def main():
    if len(sys.argv) < 2:
        print("Gamit: python diagnose_employee.py <employee_id>")
        sys.exit(1)

    employee_id = sys.argv[1]

    # Importing dito (hindi sa taas) sabay ng app startup, dahil ang
    # deepfacerecog_controller ay nagpapatakbo ng load_known_faces() sa
    # module-level pagka-import - gusto nating makuha ang parehong
    # in-memory na estado na ginagamit din ng aktwal na Flask app kung
    # ito ay tumatakbo sa parehong proseso/session. Kung hiwalay na
    # proseso ito (hal. bagong `python` invocation habang tumatakbo pa
    # rin ang Flask server sa ibang proseso), ang known_face_* ay
    # kokopyahin/muling ilo-load rito nang hiwalay - kaya ang resulta ng
    # "currently_loaded_in_memory_for_scanning" dito ay sumasalamin sa
    # ISANG BAGONG reload, hindi kinakailangan sa live na Flask process.
    from registerface_controller import RegisterFaceController

    result = RegisterFaceController.diagnose_employee_face(employee_id)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()