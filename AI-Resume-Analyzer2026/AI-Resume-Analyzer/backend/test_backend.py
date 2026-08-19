import sys
import os
import json
import io
import zipfile
import xml.etree.ElementTree as ET
sys.path.append(os.path.dirname(__file__))

import database
import services

def test_db_setup():
    try:
        # Initialize SQLite DB
        database.init_db()
        print("[OK] Database tables initialized successfully (SQLite3).")
    except Exception as e:
        print(f"[ERROR] Database initialization failed: {e}")
        sys.exit(1)

def test_mock_analysis():
    try:
        resume_text = "John Doe is a Senior Python Developer with Javascript and SQL skills."
        job_desc = "Looking for a Python Developer who knows SQL and AWS."
        
        # Test mock analysis fallback
        result = services.get_mock_analysis(resume_text, job_desc)
        
        # Verify fields
        assert "overall_score" in result, "overall_score is missing"
        assert len(result["skills"]) > 0, "skills list is empty"
        assert result["job_description_match"] is not None, "job description match object is missing"
        assert result["job_description_match"]["match_score"] > 0, "match score is invalid"
        
        print("[OK] Mock Analysis fallback working properly.")
        print(f"  - Overall Score: {result['overall_score']}")
        print(f"  - Extracted Skills: {result['skills']}")
        print(f"  - Match Score: {result['job_description_match']['match_score']}")
    except Exception as e:
        print(f"[ERROR] Mock Analysis test failed: {e}")
        sys.exit(1)

def test_docx_parser():
    try:
        # Create a mock docx in memory (zip file containing word/document.xml)
        docx_bytes = io.BytesIO()
        with zipfile.ZipFile(docx_bytes, 'w') as mock_zip:
            # Word XML document structure
            doc_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>'
                '<w:p><w:r><w:t>John Doe Resume</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>Python Developer</w:t></w:r></w:p>'
                '</w:body>'
                '</w:document>'
            )
            mock_zip.writestr("word/document.xml", doc_xml)
        
        extracted_text = services.extract_text_from_docx(docx_bytes.getvalue())
        assert "John Doe Resume" in extracted_text, "Failed to extract paragraph 1"
        assert "Python Developer" in extracted_text, "Failed to extract paragraph 2"
        print("[OK] Pure-Python DOCX text extractor working properly (zero-dependency).")
    except Exception as e:
        print(f"[ERROR] DOCX parser test failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    print("Starting backend logic verification...")
    test_db_setup()
    test_mock_analysis()
    test_docx_parser()
    print("[OK] All tests passed!")
