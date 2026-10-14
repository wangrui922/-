-- Preserve handwritten solution images with each mistake.
ALTER TABLE mistakes ADD COLUMN work_image_urls TEXT;
