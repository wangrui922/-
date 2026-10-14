-- Expand student profiles from primary-only to grades 1-12.
ALTER TABLE students DROP CONSTRAINT IF EXISTS ck_students_grade;
ALTER TABLE students DROP CONSTRAINT IF EXISTS ck_students_stage;

ALTER TABLE students
    ADD CONSTRAINT ck_students_grade CHECK (grade >= 1 AND grade <= 12);
ALTER TABLE students
    ADD CONSTRAINT ck_students_stage CHECK (stage IN ('primary','junior','senior'));

UPDATE students
SET stage = CASE
    WHEN grade <= 6 THEN 'primary'
    WHEN grade <= 9 THEN 'junior'
    ELSE 'senior'
END;
