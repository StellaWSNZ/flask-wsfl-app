import os
import hashlib
from pathlib import Path

import pandas as pd


ANON_DATA_DIR = Path("app/static/anonymous_data")
FAKE_SCHOOLS_PATH = ANON_DATA_DIR / "school.csv"

CLASS_NAMES = [f"Room {i}" for i in range(1, 21)]


def demo_mode_on() -> bool:
    return os.getenv("DEMO_MODE", "0") == "1"


def load_alt_names(name_type: str = "student") -> pd.DataFrame:
    files = {
        "student": ANON_DATA_DIR / "student.csv",
        "teacher": ANON_DATA_DIR / "teacher.csv",
    }

    if name_type not in files:
        raise ValueError(f"Unknown anonymous name type: {name_type}")

    return pd.read_csv(files[name_type])


def stable_index(value, length: int) -> int:
    if pd.isna(value) or length == 0:
        return 0

    value = str(value).encode("utf-8")
    digest = hashlib.sha256(value).hexdigest()

    return int(digest, 16) % length


def sort_records(records, name_col):
    if not records:
        return records

    return sorted(
        records,
        key=lambda row: str(row.get(name_col, "")).lower()
    )


def sort_student_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    sort_cols = [
        col for col in ["LastName", "PreferredName", "FirstName"]
        if col in df.columns
    ]

    if not sort_cols:
        return df

    return df.sort_values(sort_cols, na_position="last").reset_index(drop=True)


def get_fake_identity(real_value, name_type: str = "student") -> dict:
    fake_names = load_alt_names(name_type)
    idx = stable_index(real_value, len(fake_names))
    row = fake_names.iloc[idx]

    return {
        "FirstName": row["FirstName"],
        "LastName": row["LastName"],
        "PreferredName": row["PreferredName"],
    }


def fake_full_name(value, name_type: str = "student") -> str:
    if pd.isna(value):
        return value

    fake = get_fake_identity(value, name_type)
    return f"{fake['FirstName']} {fake['LastName']}"


def fake_email_from_value(value, name_type: str = "teacher") -> str:
    if pd.isna(value):
        return value

    fake = get_fake_identity(value, name_type)

    first = str(fake["PreferredName"]).lower()
    last = str(fake["LastName"]).lower()

    return f"{first}.{last}@example.com"


def fake_nsn(value) -> str:
    if pd.isna(value):
        return value

    idx = stable_index(value, 999999)
    return str(999000000 + idx)


def fake_birthdate(value):
    if pd.isna(value):
        return value

    idx = stable_index(value, 365)
    start = pd.Timestamp("2013-01-01")

    return (start + pd.Timedelta(days=idx)).date()


def fake_class_name(class_id, moe_number=None) -> str:
    key = f"{moe_number}_{class_id}" if moe_number is not None else str(class_id)
    idx = stable_index(key, len(CLASS_NAMES))

    return CLASS_NAMES[idx]


def get_fake_school_name(moe_number):
    if not demo_mode_on:
        return moe_number
    if pd.isna(moe_number):
        return moe_number

    fake_schools = pd.read_csv(FAKE_SCHOOLS_PATH)

    row = fake_schools.loc[
        fake_schools["MOENumber"].astype(str) == str(moe_number)
    ]

    if row.empty:
        idx = stable_index(moe_number, 9999) + 1
        return f"School {idx}"

    return row.iloc[0]["SchoolName"]

def anonymise_entity_name(entity_id, entity_type, original_value=None):

    if original_value:
        original_clean = str(original_value).strip()
        original_lower = original_clean.lower()

        # Keep plain system labels only
        if original_lower in {"kaiako led", "unassigned"}:
            return original_clean

    prefixes = {
        "School": "School",
        "Provider": "Provider",
        "Funder": "Funder",
        "Group": "Group",
    }

    prefix = prefixes.get(entity_type, "Entity")
    idx = stable_index(entity_id, 9999) + 1

    return f"{prefix} {idx}"

def anonymise_students_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    df = df.copy()
    fake_names = load_alt_names("student").to_dict("records")

    used_fake_indexes = set()
    fake_identities = {}

    def identity_key(row):
        if "NSN" in row and pd.notna(row["NSN"]):
            return str(row["NSN"])

        parts = []
        for col in ["FirstName", "LastName", "PreferredName", "DateOfBirth"]:
            if col in row and pd.notna(row[col]):
                parts.append(str(row[col]))

        return "|".join(parts)

    def get_unique_fake_identity(key):
        if key in fake_identities:
            return fake_identities[key]

        start_idx = stable_index(key, len(fake_names))

        for offset in range(len(fake_names)):
            idx = (start_idx + offset) % len(fake_names)

            if idx not in used_fake_indexes:
                used_fake_indexes.add(idx)
                fake_identities[key] = fake_names[idx]
                return fake_names[idx]

        idx = start_idx
        fake_identities[key] = fake_names[idx]
        return fake_names[idx]

    for idx, row in df.iterrows():
        key = identity_key(row)
        fake = get_unique_fake_identity(key)

        if "FirstName" in df.columns:
            df.at[idx, "FirstName"] = fake["FirstName"]

        if "LastName" in df.columns:
            df.at[idx, "LastName"] = fake["LastName"]

        if "PreferredName" in df.columns:
            df.at[idx, "PreferredName"] = fake["PreferredName"]

        if "Email" in df.columns:
            first = str(fake["PreferredName"]).lower()
            last = str(fake["LastName"]).lower()
            df.at[idx, "Email"] = f"{first}.{last}@example.com"

        if "NSN" in df.columns:
            df["NSN"] = df["NSN"].astype("object")

        if "NSN" in df.columns and pd.notna(row.get("NSN")):
            df.at[idx, "NSN"] = str(fake_nsn(row["NSN"]))

        if "DateOfBirth" in df.columns and pd.notna(row.get("DateOfBirth")):
            df.at[idx, "DateOfBirth"] = fake_birthdate(row["DateOfBirth"])

    return df


def fake_staff_identity_from_row(row):
    parts = []

    for col in ["Email", "AlternateEmail", "AboutEmail", "FirstName", "Surname", "LastName", "Name", "StaffName", "TeacherName", "FullName"]:
        if col in row and pd.notna(row[col]):
            parts.append(str(row[col]))

    key = "|".join(parts)
    fake = get_fake_identity(key, "teacher")

    return fake


def anonymise_staff_df(df: pd.DataFrame) -> pd.DataFrame:
    if (df is None or df.empty) or not demo_mode_on():
        return df

    df = df.copy()

    for idx, row in df.iterrows():
        fake = fake_staff_identity_from_row(row)

        if "FirstName" in df.columns:
            df.at[idx, "FirstName"] = fake["FirstName"]

        if "Surname" in df.columns:
            df.at[idx, "Surname"] = fake["LastName"]

        if "LastName" in df.columns:
            df.at[idx, "LastName"] = fake["LastName"]

        if "PreferredName" in df.columns:
            df.at[idx, "PreferredName"] = fake["PreferredName"]

        fake_full = f"{fake['FirstName']} {fake['LastName']}"

        for col in ["Name", "StaffName", "TeacherName", "FullName"]:
            if col in df.columns:
                df.at[idx, col] = fake_full

        fake_email = f"{str(fake['PreferredName']).lower()}.{str(fake['LastName']).lower()}@example.com"

        for col in ["Email", "AlternateEmail", "AboutEmail"]:
            if col in df.columns and pd.notna(row.get(col)):
                df.at[idx, col] = fake_email

    return df


def anonymise_class_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    df = df.copy()

    teacher_names = load_alt_names("teacher").to_dict("records")
    teacher_map = {}
    used_teacher_indexes = set()

    def get_teacher(real_value):
        if pd.isna(real_value):
            return real_value

        real_value = str(real_value)

        if real_value in teacher_map:
            return teacher_map[real_value]

        start_idx = stable_index(real_value, len(teacher_names))

        for offset in range(len(teacher_names)):
            idx = (start_idx + offset) % len(teacher_names)

            if idx not in used_teacher_indexes:
                used_teacher_indexes.add(idx)

                fake = teacher_names[idx]
                teacher_map[real_value] = (
                    f"{fake['FirstName']} {fake['LastName']}"
                )

                return teacher_map[real_value]

        fake = teacher_names[start_idx]
        teacher_map[real_value] = f"{fake['FirstName']} {fake['LastName']}"

        return teacher_map[real_value]

    def get_class(class_id, moe_number=None, fallback_value=None):
        if pd.isna(class_id):
            class_key = str(fallback_value) if pd.notna(fallback_value) else ""
        else:
            class_key = str(class_id)

        if moe_number is not None and pd.notna(moe_number):
            class_key = f"{moe_number}_{class_key}"

        idx = stable_index(class_key, len(CLASS_NAMES))
        return CLASS_NAMES[idx]

    if "TeacherName" in df.columns:
        df["TeacherName"] = df["TeacherName"].apply(get_teacher)

    if "ClassName" in df.columns:
        if {"ClassID", "MOENumber"} <= set(df.columns):
            df["ClassName"] = df.apply(
                lambda row: get_class(
                    class_id=row["ClassID"],
                    moe_number=row["MOENumber"],
                    fallback_value=row["ClassName"],
                ),
                axis=1,
            )

        elif "ClassID" in df.columns:
            df["ClassName"] = df.apply(
                lambda row: get_class(
                    class_id=row["ClassID"],
                    fallback_value=row["ClassName"],
                ),
                axis=1,
            )

        else:
            df["ClassName"] = df["ClassName"].apply(
                lambda value: get_class(
                    class_id=None,
                    fallback_value=value,
                )
            )

    if "ClassName" in df.columns:
        df = df.sort_values(["ClassName"], na_position="last").reset_index(drop=True)

    return df


def anonymise_class_details(class_id, moe_number, class_name, teacher_name):
    return (
        fake_class_name(class_id, moe_number),
        fake_full_name(teacher_name, "teacher"),
    )


def anonymise_school_entities(entities):
    if not demo_mode_on() or not entities:
        return entities

    for entity in entities:

        school_id = (
            entity.get("id")
            or entity.get("ID")
            or entity.get("MOENumber")
        )

        fake_name = get_fake_school_name(school_id)

        for col in ["description", "Description", "SchoolName", "School", "Name"]:
            if col in entity:
                entity[col] = fake_name

    sort_col = (
        "SchoolName"
        if any("SchoolName" in e for e in entities)
        else "description"
    )

    return sort_records(entities, sort_col)


def anonymise_school_list(records, id_col="MOENumber", name_col="School"):
    if not demo_mode_on() or not records:
        return records

    for row in records:
        if id_col in row and name_col in row:
            row[name_col] = get_fake_school_name(row[id_col])

    return sort_records(records, name_col)


def anonymise_class_list(records, moe_number=None):
    if not demo_mode_on() or not records:
        return records

    df = pd.DataFrame(records)

    if moe_number is not None and "MOENumber" not in df.columns:
        df["MOENumber"] = moe_number

    df = anonymise_class_dataframe(df)

    if "ClassName" in df.columns:
        df = df.sort_values(["ClassName"], na_position="last")

    elif "name" in df.columns:
        df = df.sort_values(["name"], na_position="last")

    return df.reset_index(drop=True).to_dict(orient="records")


def anonymise_student_list(records):
    if not demo_mode_on() or not records:
        return records

    df = pd.DataFrame(records)
    df = anonymise_students_df(df)
    df = sort_student_df(df)

    return df.to_dict(orient="records")

def anonymise_entities(entities, entity_type):
    if not demo_mode_on() or not entities:
        return entities

    if entity_type == "School":
        entities = anonymise_school_entities(entities)

    elif entity_type in ["Provider", "Funder", "Group"]:
        for entity in entities:
            original = entity.get("description") or entity.get("Description")

            id_value = (
                entity.get(f"{entity_type}ID")
                or entity.get("id")
                or entity.get("ID")
                or entity.get("Number")
                or original
            )

            fake_name = anonymise_entity_name(
                id_value,
                entity_type,
                original_value=original
            )

            if "description" in entity:
                entity["description"] = fake_name

            if "Description" in entity:
                entity["Description"] = fake_name

        entities = sort_records(entities, "description")

    return entities
def anonymise_df(
    df,
    School_desc="School",
    School_id="MOENumber",
    Provider_desc="Provider",
    Provider_id="ProviderID",
    Funder_desc="Funder",
    Funder_id="FunderID",
):
    if df is None or df.empty:
        return df

    if not demo_mode_on():
        return df

    df = df.copy()

    # School should use fake school.csv names
    if School_desc in df.columns:
        if School_id in df.columns:
            df[School_desc] = df[School_id].apply(get_fake_school_name)
        else:
            df[School_desc] = df[School_desc].apply(get_fake_school_name)

    # Provider/Funder can use numbered anonymous names
    def anonymise_desc_from_id(desc_col, id_col, entity_type):
        if desc_col in df.columns and id_col in df.columns:
            df[desc_col] = df[id_col].apply(
                lambda x: anonymise_entity_name(x, entity_type)
            )
        elif desc_col in df.columns:
            df[desc_col] = df[desc_col].apply(
                lambda x: anonymise_entity_name(x, entity_type)
            )

    anonymise_desc_from_id(Provider_desc, Provider_id, "Provider")
    anonymise_desc_from_id(Funder_desc, Funder_id, "Funder")

    return df

def anonymise_funder_school_rows(rows):
    if not demo_mode_on() or not rows:
        return rows

    rows = list(rows)

    for r in rows:
        # School name
        if r.get("MOENumber") is not None:
            r["SchoolName"] = get_fake_school_name(r["MOENumber"])

        # Contact/staff JSON lists
        for key in ["Contacts", "InactiveContacts", "OtherStaff"]:
            if r.get(key):
                r[key] = anonymise_staff_df(pd.DataFrame(r[key])).to_dict("records")

        # ClassCounts nested providers
        for c in r.get("ClassCounts", []) or []:
            prov = c.get("Providers")
            if prov and prov != "Unassigned":
                c["Providers"] = anonymise_entity_name(prov, "Provider")

        # Selected provider
        prov = r.get("SelectedProviders")
        if prov and prov != "Unassigned":
            r["SelectedProviders"] = anonymise_entity_name(prov, "Provider")

    return sort_records(rows, "SchoolName")


def anonymise_named_list(rows, name_cols):
    if not rows or not demo_mode_on():
        return rows

    out = []

    for i, row in enumerate(rows, start=1):
        row = dict(row)

        for col in name_cols:
            if col in row and row[col]:
                row[col] = f"Demo {col} {i}"

        out.append(row)

    return out

def anonymise_staff_list(rows):
    if not rows or not demo_mode_on():
        return rows

    out = []

    teacher_names = load_alt_names("teacher")

    for i, row in enumerate(rows):
        row = dict(row)

        fake = teacher_names.iloc[i % len(teacher_names)]

        first = fake["FirstName"]
        last = fake["LastName"]

        if "FirstName" in row:
            row["FirstName"] = first

        if "LastName" in row:
            row["LastName"] = last

        if "Surname" in row:
            row["Surname"] = last

        if "Name" in row:
            row["Name"] = f"{first} {last}"

        if "Email" in row:
            row["Email"] = f"{first.lower()}.{last.lower()}@example.com"

        if "AlternateEmail" in row:
            row["AlternateEmail"] = f"{first.lower()}.{last.lower()}2@example.co.nz"

        out.append(row)

    return out

def fake_bulk_entity_label(entity_id, entity_type=None):
    prefix = "Entity"
    if entity_type:
        prefix = str(entity_type).title()
    return f"{prefix} {stable_index(entity_id, 9999) + 1}"


def anonymise_bulk_email_entities(entities):
    if not demo_mode_on() or not entities:
        return entities

    out = []
    for e in entities:
        e = dict(e)
        e["Description"] = fake_bulk_entity_label(
            e.get("id"),
            e.get("Type")
        )
        out.append(e)

    return out

def anonymise_bulk_email_rows(rows):
    if not demo_mode_on() or not rows:
        return rows

    df = pd.DataFrame(rows)

    df = anonymise_staff_df(df)

    for idx, row in df.iterrows():
        entity_key = (
            row.get("EntityID")
            or row.get("EntityDesc")
        )

        if "Email" in df.columns:
            first = str(row.get("FirstName") or "demo").strip().lower()
            last = str(row.get("Surname") or row.get("LastName") or "user").strip().lower()

            first = first.replace(" ", "")
            last = last.replace(" ", "")

            df.at[idx, "Email"] = f"{first}.{last}@example.com"

        if "EntityDesc" in df.columns:
            df.at[idx, "EntityDesc"] = fake_bulk_entity_label(
                entity_key,
                row.get("EntityType")
            )

    return df.to_dict("records")

def anonymise_survey_details(title, details, subject_first, subject_last, reviewer_first, reviewer_last):

    if not demo_mode_on():
        return details

    title = title or ""
    details = details or ""

    subject_name = f"{subject_first} {subject_last}".strip()
    reviewer_name = f"{reviewer_first} {reviewer_last}".strip()

    fake_subject = "Teacher A"
    fake_reviewer = "Teacher B"

    if title == "Self Review":
        return f"Self Review by {fake_reviewer}"

    if "Teacher Assessment" in title:
        return f"{title} about {fake_subject} by {fake_reviewer}"

    if "External Review" in title or "Extenal Review" in title:
        return f"{title} about {fake_subject} by {fake_reviewer}"

    return details

def anonymise_student_records(records):
    if not demo_mode_on() or not records:
        return records

    fake_names = load_alt_names("student")
    out = []

    for i, row in enumerate(records):
        row = dict(row)

        key = row.get("NSN") or i
        fake = fake_names.iloc[int(key) % len(fake_names)]

        first = fake.get("FirstName", f"Student{i + 1}")
        last = fake.get("LastName", "Learner")

        row["FirstName"] = first
        row["PreferredName"] = first
        row["LastName"] = last

        if row.get("NSN") is not None:
            row["NSN"] = 900000000 + i

        # Optional: safer to hide actual DOB
        if "DateOfBirth" in row:
            row["DateOfBirth"] = ""

        out.append(row)

    return out


import pandas as pd

def anonymise_student_rows(rows):
    if not demo_mode_on() or not rows:
        return rows

    df = pd.DataFrame(rows)

    for idx, row in df.iterrows():

        student_key = (
            row.get("NSN")
            or row.get("StudentID")
            or f"{row.get('FirstName', '')}|{row.get('LastName', '')}"
        )

        fake = get_fake_identity(student_key, "student")

        if "FirstName" in df.columns:
            df.at[idx, "FirstName"] = fake["FirstName"]

        if "PreferredName" in df.columns:
            df.at[idx, "PreferredName"] = fake["PreferredName"]

        if "LastName" in df.columns:
            df.at[idx, "LastName"] = fake["LastName"]

        if "FullName" in df.columns:
            df.at[idx, "FullName"] = (
                f"{fake['FirstName']} {fake['LastName']}"
            )

        if "NSN" in df.columns:
            df["NSN"] = df["NSN"].astype("object")

        if "NSN" in df.columns and pd.notna(row.get("NSN")):
            df.at[idx, "NSN"] = str(fake_nsn(row["NSN"]))

        if "DateOfBirth" in df.columns:
            df.at[idx, "DateOfBirth"] = ""

    return df.to_dict("records")


def anonymise_student_search_rows(rows, query=None):
    if not demo_mode_on() or not rows:
        return rows

    df = pd.DataFrame(rows)

    if "NSN" in df.columns:
        df["NSN"] = df["NSN"].astype("object")

    q = (query or "").strip()
    q_lower = q.lower()

    fake_names = load_alt_names("student")

    used_fake_names = set()

    for idx, row in df.iterrows():

        student_key = (
            row.get("NSN")
            or row.get("StudentID")
            or f"{row.get('FirstName', '')}|{row.get('LastName', '')}|{row.get('DateOfBirth', '')}"
        )

        fake = get_fake_identity(student_key, "student")

        fake_first = str(fake["FirstName"])
        fake_preferred = str(fake["PreferredName"])
        fake_last = str(fake["LastName"])

        # Make demo search results visually match the search query
        if q_lower:
            matching_fake_names = fake_names[
                fake_names["FirstName"].astype(str).str.lower().str.startswith(q_lower)
                | fake_names["PreferredName"].astype(str).str.lower().str.startswith(q_lower)
                | fake_names["LastName"].astype(str).str.lower().str.startswith(q_lower)
            ]

            if not matching_fake_names.empty:

                start_idx = stable_index(student_key, len(matching_fake_names))

                matched_fake = None

                for offset in range(len(matching_fake_names)):
                    real_idx = (start_idx + offset) % len(matching_fake_names)

                    candidate = matching_fake_names.iloc[real_idx]

                    candidate_key = (
                        str(candidate["FirstName"]).strip().lower(),
                        str(candidate["LastName"]).strip().lower(),
                        str(candidate["PreferredName"]).strip().lower(),
                    )

                    if candidate_key not in used_fake_names:
                        matched_fake = candidate
                        used_fake_names.add(candidate_key)
                        break

                if matched_fake is not None:
                    fake_first = str(matched_fake["FirstName"])
                    fake_preferred = str(matched_fake["PreferredName"])
                    fake_last = str(matched_fake["LastName"])

            else:
                clean_query = q[:20].title()

                fake_first = clean_query
                fake_preferred = clean_query

        fake_identity_key = (
            fake_first.strip().lower(),
            fake_last.strip().lower(),
            fake_preferred.strip().lower(),
        )

        if fake_identity_key in used_fake_names:
            continue

        used_fake_names.add(fake_identity_key)

        if "FirstName" in df.columns:
            df.at[idx, "FirstName"] = fake_first

        if "PreferredName" in df.columns:
            df.at[idx, "PreferredName"] = fake_preferred

        if "LastName" in df.columns:
            df.at[idx, "LastName"] = fake_last

        if "FullName" in df.columns:
            df.at[idx, "FullName"] = f"{fake_preferred} {fake_last}"

        if "Name" in df.columns:
            df.at[idx, "Name"] = f"{fake_preferred} {fake_last}"

        if "NSN" in df.columns and pd.notna(row.get("NSN")):
            df.at[idx, "NSN"] = str(fake_nsn(row["NSN"]))

        if "DateOfBirth" in df.columns:
            df.at[idx, "DateOfBirth"] = ""
            if "LatestSchoolName" in df.columns:

                school_id = (
                    row.get("LatestMOENumber")
                    or row.get("MOENumber")
                    or row.get("SchoolID")
                    or row.get("SchoolName")
                )

                df.at[idx, "LatestSchoolName"] = get_fake_school_name(school_id)
    out = df.to_dict("records")

    seen = set()
    unique_rows = []

    for row in out:

        key = (
            str(row.get("FirstName", "")).strip().lower(),
            str(row.get("LastName", "")).strip().lower(),
            str(row.get("PreferredName", "")).strip().lower(),
        )

        if key in seen:
            continue

        seen.add(key)
        unique_rows.append(row)

    return unique_rows