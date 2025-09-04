# Gemini Project Context: apiFlaskManngo

This document provides a comprehensive overview of the `apiFlaskManngo` project, designed to assist AI agents in understanding and interacting with the codebase.

## 1. Project Overview

This project is a comprehensive RESTful API built with Python and the Flask framework. It serves as the backend for "Manngo," a business management application. The API manages a wide range of operations, including inventory, sales, customer and supplier management, financial records, and user authentication.

### Key Technologies

- **Backend Framework:** Flask, Flask-RESTful
- **Database:** PostgreSQL (interfaced via Flask-SQLAlchemy)
- **Object-Relational Mapper (ORM):** SQLAlchemy
- **Database Migrations:** Flask-Migrate (using Alembic)
- **Authentication:** JSON Web Tokens (JWT) via Flask-JWT-Extended
- **Serialization/Deserialization:** Marshmallow (via flask-marshmallow and marshmallow-sqlalchemy)
- **File Storage:** AWS S3 for handling file uploads (e.g., payment receipts, product images).
- **Deployment:** Docker and Gunicorn
- **Dependencies:** Managed in `requirements.txt`.

### Architecture

The application follows a clean, resource-based architecture:

- **`app.py`**: The main entry point, responsible for Flask app initialization, configuration, and extension setup.
- **`models.py`**: Defines the database schema using SQLAlchemy ORM models.
- **`schemas.py`**: Contains Marshmallow schemas for serializing and deserializing data, ensuring a consistent API data structure.
- **`resources/`**: A directory containing individual files for each API resource (e.g., `producto_resource.py`, `venta_resource.py`). This modular approach keeps the codebase organized.
- **`extensions.py`**: Initializes and configures Flask extensions (like `db`, `jwt`, `supabase`).
- **`common.py`**: Likely contains shared utilities, such as custom decorators (`@rol_requerido`) and constants.
- **`Dockerfile`**: Defines a multi-stage Docker build for creating a production-ready container image.

## 2. Building and Running

### Local Development Setup

1.  **Environment Variables:**
    The application requires environment variables for configuration. Create a `.env` file in the project root. A minimal configuration would be:

    ```env
    # Flask Configuration
    FLASK_APP=app.py
    FLASK_ENV=development
    
    # Database URL (replace with your local PostgreSQL connection string)
    DATABASE_URL=postgresql://user:password@localhost:5432/manngo_db
    
    # JWT Secret Key (change this to a long, random string)
    JWT_SECRET_KEY=your-super-secret-and-long-jwt-key
    
    # AWS S3 Configuration (optional for some local tasks, but needed for file uploads)
    S3_BUCKET=your-s3-bucket-name
    AWS_REGION=your-aws-region
    
    # Supabase & Google AI (if using related features)
    SUPABASE_URL=your-supabase-url
    SUPABASE_KEY=your-supabase-key
    GOOGLE_API_KEY=your-google-api-key
    ```

2.  **Install Dependencies:**
    It is recommended to use a virtual environment.

    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    pip install -r requirements.txt
    ```

3.  **Run Database Migrations:**
    To create the database schema based on the models:

    ```bash
    flask db upgrade
    ```

4.  **Run the Development Server:**

    ```bash
    flask run
    ```
    The API will be available at `http://127.0.0.1:5000`.

### Running with Docker

1.  **Build the Docker Image:**

    ```bash
    docker build -t manngo-api .
    ```

2.  **Run the Docker Container:**
    Ensure your `.env` file is present in the root directory.

    ```bash
    docker run -p 8080:8080 --env-file .env manngo-api
    ```
    The API will be available at `http://localhost:8080`.

### Testing

The project includes a `tests/` directory. While specific test commands are not defined, `pytest` is a standard choice for Flask applications.

```bash
# Run tests (assuming pytest is used)
pytest
```

## 3. Development Conventions

-   **API Design:** The API is RESTful, using standard HTTP methods (`GET`, `POST`, `PUT`, `DELETE`) for resource manipulation. Data is exchanged in JSON format.
-   **Authentication:** Endpoints are secured using JWT. A valid `access_token` must be provided in the `Authorization: Bearer <token>` header for protected resources.
-   **Authorization:** Access is controlled by user roles (`admin`, `gerente`, `usuario`). The `@rol_requerido` decorator is used to enforce role-based permissions on specific endpoints.
-   **Database:** All database interactions are performed through SQLAlchemy models. Schema changes should be managed via `flask-migrate` commands (`flask db migrate`, `flask db upgrade`).
-   **Serialization:** Marshmallow schemas (`schemas.py`) define the public structure of API resources. This decouples the database models from the API representation and handles data validation.
-   **File Uploads:** Files are not stored on the local filesystem in production. They are uploaded directly to an AWS S3 bucket, and the database stores the URL or key.
-   **Code Style:** The code is modular, with each resource and its logic isolated in its own file within the `resources/` directory. Shared logic and configurations are placed in dedicated files (`extensions.py`, `common.py`).
-   **API Documentation:** The `API_OVERVIEW.md` file provides detailed documentation for all available endpoints, including request/response schemas and authorization rules. This should be consulted before making changes to the API.
