# Music Search API

A FastAPI-based application for searching music tracks, including integration with Beatport.

## Setup

1.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
2.  **Environment Variables:**
    Create a `.env` file with the following:
    ```env
    MONGO_URL=mongodb://localhost:27017
    MONGO_DB=music_search_db
    MONGO_COL=search_history
    PORT=8111
    # Playwright configuration
    BASE_URL=https://www.beatport.com
    USER_AGENT="Mozilla/5.0 ..."
    ```
3.  **Run the Application:**
    ```bash
    python main.py
    ```

## API Endpoints

### General

*   `GET /`: Service status.
*   `GET /health`: Database connection health check.

### Search

*   `GET /search?track={query}`: Search for tracks (scrapes external sources).
*   `GET /track/{track_key}`: Get specific track details by key.

### Beatport

*   `GET /beatport/track-id`
    *   **Description:** Find a Beatport track ID by artist, title, and mix name.
    *   **Parameters:**
        *   `artist` (string, required): Artist name.
        *   `title` (string, required): Track title.
        *   `mix` (string, optional): Mix name (e.g., "Extended Mix").
    *   **Response:**
        ```json
        {
          "track_id": 12345678
        }
        ```

### History

*   `GET /history`: List search history.
*   `GET /history/{search_id}`: Get details of a past search.
*   `DELETE /delete`: Clear all history.
*   `DELETE /delete/{search_id}`: Delete a specific history item.
