// Local
#include "renderer.h"

// Declarations
static float ellipseCoverage(float x, float y, float radiusX, float radiusY);

// Bend a mesh of the ellipse texture through the face rotation
void Ellipse::render(SDL_Renderer* renderer, const Vec3& facePosition, const Vec3& faceRotation) {
    // Make the texture the first time the ellipse is drawn
    if (!texture)
        buildTexture(renderer);

    // Project a grid of points across the ellipse onto the screen
    SDL_Vertex vertices[(MESH_COLUMNS + 1) * (MESH_ROWS + 1)];
    for (int row = 0; row <= MESH_ROWS; row++) {
        for (int column = 0; column <= MESH_COLUMNS; column++) {
            float acrossFraction = (float)column / MESH_COLUMNS;
            float downFraction = (float)row / MESH_ROWS;
            Vec3 point((acrossFraction * 2 - 1) * radiusX, (downFraction * 2 - 1) * radiusY, 0);
            vertices[row * (MESH_COLUMNS + 1) + column] = {Projection::project(point + localPosition + facePosition, faceRotation), fillColor, {acrossFraction, downFraction}};
        }
    }

    // Join the grid into two triangles per cell
    int indices[MESH_COLUMNS * MESH_ROWS * 6];
    int count = 0;
    for (int row = 0; row < MESH_ROWS; row++) {
        for (int column = 0; column < MESH_COLUMNS; column++) {
            int topLeft = row * (MESH_COLUMNS + 1) + column;
            int bottomLeft = topLeft + MESH_COLUMNS + 1;
            int cell[6] = {topLeft, topLeft + 1, bottomLeft, topLeft + 1, bottomLeft + 1, bottomLeft};
            for (int index : cell)
                indices[count++] = index;
        }
    }

    // Hand the mesh to the graphics card in one draw
    SDL_RenderGeometry(renderer, texture, vertices, (MESH_COLUMNS + 1) * (MESH_ROWS + 1), indices, count);
}

// Paint a white ellipse with a soft edge, and the cutout taken out of it
void Ellipse::buildTexture(SDL_Renderer* renderer) {
    // Size the texture from the ellipse and the pixels per face unit
    int width = (int)(radiusX * 2 * ELLIPSE_TEXTURE_SCALE);
    int height = (int)(radiusY * 2 * ELLIPSE_TEXTURE_SCALE);
    float scaledRadiusX = radiusX * ELLIPSE_TEXTURE_SCALE;
    float scaledRadiusY = radiusY * ELLIPSE_TEXTURE_SCALE;

    // Size the cutout the same way, it keeps the ellipse shape
    float cutoutRadiusY = cutoutHeight / 2.0f * ELLIPSE_TEXTURE_SCALE;
    float cutoutRadiusX = scaledRadiusX * cutoutRadiusY / scaledRadiusY;
    float cutoutCenterY = cutoutY * ELLIPSE_TEXTURE_SCALE;

    // Fill every pixel with white, faded by how much of it the ellipse covers
    vector<Uint32> pixels(width * height);
    for (int row = 0; row < height; row++) {
        for (int column = 0; column < width; column++) {
            float x = column + 0.5f - width / 2.0f;
            float y = row + 0.5f - height / 2.0f;
            float coverage = ellipseCoverage(x, y, scaledRadiusX, scaledRadiusY);
            if (cutoutHeight > 0)
                coverage *= 1.0f - ellipseCoverage(x, y - cutoutCenterY, cutoutRadiusX, cutoutRadiusY);
            pixels[row * width + column] = ((Uint32)(coverage * 255) << 24) | 0x00FFFFFF;
        }
    }

    // Upload it, blended and smoothed so the mesh scales cleanly
    texture = SDL_CreateTexture(renderer, SDL_PIXELFORMAT_ARGB8888, SDL_TEXTUREACCESS_STATIC, width, height);
    SDL_UpdateTexture(texture, nullptr, pixels.data(), width * sizeof(Uint32));
    SDL_SetTextureBlendMode(texture, SDL_BLENDMODE_BLEND);
    SDL_SetTextureScaleMode(texture, SDL_ScaleModeLinear);
}

// How much of a pixel the ellipse covers, from the distance to its edge
static float ellipseCoverage(float x, float y, float radiusX, float radiusY) {
    // Measure how far out the pixel is, one at the edge
    float ratio = sqrtf(x * x / (radiusX * radiusX) + y * y / (radiusY * radiusY));
    if (ratio < 0.0001f)
        return 1.0f;

    // Turn that into pixels from the edge, positive inside
    float slope = sqrtf(x * x / (radiusX * radiusX * radiusX * radiusX) + y * y / (radiusY * radiusY * radiusY * radiusY)) / ratio;
    float distance = (1.0f - ratio) / slope;

    // Fade across the one pixel at the edge
    return fmaxf(0.0f, fminf(1.0f, distance + 0.5f));
}

// Project 3D point to 2D screen coordinates with perspective
namespace Projection {
    SDL_FPoint project(const Vec3& point, const Vec3& faceRotation) {
        // Apply face rotation first
        Vec3 rotated = point.rotateX(faceRotation.x).rotateY(faceRotation.y).rotateZ(faceRotation.z);

        // Shrink with depth, grow with the screen so the face keeps its physical size, then move the origin to the middle of the screen
        float perspective = 1.0f / (1.0f + rotated.z * PERSPECTIVE_DEPTH);
        float screen_scale = screen_height / REFERENCE_SCREEN_HEIGHT;
        return {rotated.x * perspective * screen_scale + screen_width / 2, rotated.y * perspective * screen_scale + screen_height / 2};
    }
}
