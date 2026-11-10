from app.services import simpus_user_messages as m

print(m.for_patient("LUDIA KENI", m.register_outcome("dukcapil_invalid"), nik="9110086810950001"))
print(
    m.for_patient(
        "NIKANDER WEPPE",
        m.register_outcome("error", "register exited 1"),
        nik="9110071111830001",
    )
)
print(m.fill_status("failed", "patient not found"))
