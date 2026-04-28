# Data Retention Schedule

| Record type                    | Retention period                  | Trigger              | Disposal method          |
| ------------------------------ | --------------------------------- | -------------------- | ------------------------ |
| Adult clinical record          | 10 years                          | Last contact         | Anonymise or destroy     |
| Paediatric clinical record     | Until age 25                      | Date of birth        | Anonymise or destroy     |
| Mental health record           | 20 years                          | Last contact         | Anonymise or destroy     |
| Lab result                     | Same as clinical record           | Last contact         | Anonymise or destroy     |
| Prescription / dispense        | Same as clinical record           | Last contact         | Anonymise or destroy     |
| Invoice / payment / receipt    | 7 years                           | Issue date           | Destroy                  |
| Audit log entry                | 7 years                           | Event date           | Destroy                  |
| Staff account                  | While employed + 6 months         | Departure date       | Disable, anonymise       |
| Backup snapshot                | 90 days rolling                   | Creation date        | Overwrite / destroy      |

## Implementation

Retention enforcement is **manual at this stage**: a quarterly review by the
Admin role identifies records past retention. A future iteration may add a
dedicated `manage.py` command. Soft-deleted patients (`Patient.is_deleted`)
are flagged but not erased; the periodic review decides whether to anonymise
identifying fields while preserving statistical records.

## Anonymisation procedure

When a record passes retention:

1. Replace `full_name`, `phone`, `national_id`, `next_of_kin_*` with a
   constant placeholder (e.g. `"REDACTED"`).
2. Set `photo` to null and delete the underlying file.
3. Keep `patient_number`, `date_of_birth` (year only), `sex`, and clinical
   fields for statistical purposes.
4. Add an `AuditLog` entry with `action="DELETE"` and metadata describing
   the retention basis.
