#pragma once

// SDL
#include <SDL2/SDL.h>

// Local
#include "screen.h"

// System
#include <cmath>
#include <vector>

// Namespace
using namespace std;

// Shapes further back shrink by this much per unit of depth
static const float PERSPECTIVE_DEPTH = 0.001f;

// Texture pixels per face unit, so the soft edge stays crisp
static const int ELLIPSE_TEXTURE_SCALE = 2;

// Mesh cells across and down each shape, enough to bend smoothly with perspective
static const int MESH_COLUMNS = 6;
static const int MESH_ROWS = 10;

// 3D Vector class for transformations
struct Vec3 {
    float x, y, z;

    // Build a vector, defaulting to the origin
    Vec3(float x = 0, float y = 0, float z = 0) : x(x), y(y), z(z) {}

    // Add two vectors
    Vec3 operator+(const Vec3& other) const {
        return Vec3(x + other.x, y + other.y, z + other.z);
    }

    // Scale a vector
    Vec3 operator*(float scalar) const {
        return Vec3(x * scalar, y * scalar, z * scalar);
    }

    // Turn about the X axis, in degrees
    Vec3 rotateX(float angle) const {
        float radians = angle * M_PI / 180.0f;
        float cosine = cos(radians);
        float sine = sin(radians);
        return Vec3(x, y * cosine - z * sine, y * sine + z * cosine);
    }

    // Turn about the Y axis, in degrees
    Vec3 rotateY(float angle) const {
        float radians = angle * M_PI / 180.0f;
        float cosine = cos(radians);
        float sine = sin(radians);
        return Vec3(x * cosine + z * sine, y, -x * sine + z * cosine);
    }

    // Turn about the Z axis, in degrees
    Vec3 rotateZ(float angle) const {
        float radians = angle * M_PI / 180.0f;
        float cosine = cos(radians);
        float sine = sin(radians);
        return Vec3(x * cosine - y * sine, x * sine + y * cosine, z);
    }
};

// Projection utilities
namespace Projection {
    // Project 3D point to 2D screen coordinates with perspective
    SDL_FPoint project(const Vec3& point, const Vec3& faceRotation);
}

// Base class for all vector shapes
class VectorShape {
public:
    // Where the shape sits on the face plane, and what colour it is
    Vec3 localPosition;
    SDL_Color fillColor;

    // Whether the shape is drawn at all
    bool visible;

    // Start centred, black, and shown
    VectorShape() : localPosition(0, 0, 0), fillColor({0, 0, 0, 255}), visible(true) {}
    virtual ~VectorShape() {}

    // Each shape paints itself against the face position and rotation
    virtual void render(SDL_Renderer* renderer, const Vec3& facePosition, const Vec3& faceRotation) = 0;
};

// Ellipse shape, drawn as a textured mesh so the graphics card does the filling
class Ellipse : public VectorShape {
public:
    float radiusX, radiusY;

    // Centre and height of an ellipse cut out of this one, for the mouth, set before the first draw
    float cutoutY;
    float cutoutHeight;

    // Build an ellipse of the given radii and colour, with no cutout
    Ellipse(float radiusX, float radiusY, SDL_Color fillColor)
        : radiusX(radiusX), radiusY(radiusY), cutoutY(0), cutoutHeight(0), texture(nullptr) {
        this->fillColor = fillColor;
    }

    // Bend a mesh of the ellipse texture through the face rotation
    void render(SDL_Renderer* renderer, const Vec3& facePosition, const Vec3& faceRotation) override;

private:
    // Soft edged white ellipse, made on first draw and freed with the renderer
    SDL_Texture* texture;
    void buildTexture(SDL_Renderer* renderer);
};

// Vector face class to manage all face elements
class VectorFace {
private:
    // Shapes the face owns, and where the whole face sits
    vector<VectorShape*> shapes;
    Vec3 position;
    Vec3 rotation;

public:
    // Start the face centred and facing forward
    VectorFace() : position(0, 0, 0), rotation(0, 0, 0) {}

    // Free every shape handed over
    ~VectorFace() {
        for (auto shape : shapes) {
            delete shape;
        }
    }

    // Take ownership of a shape
    void addShape(VectorShape* shape) {
        shapes.push_back(shape);
    }

    // Turn the whole face
    void setRotation(const Vec3& rotation) {
        this->rotation = rotation;
    }

    // Draw every visible shape
    void render(SDL_Renderer* renderer) {
        for (auto shape : shapes) {
            if (shape->visible)
                shape->render(renderer, position, rotation);
        }
    }
};

// Vector renderer manager
class VectorRenderer {
private:
    // The one face this renderer draws
    VectorFace face;

public:
    // Hand a shape to the face
    void addShape(VectorShape* shape) {
        face.addShape(shape);
    }

    // Draw the face
    void render(SDL_Renderer* renderer) {
        face.render(renderer);
    }

    // Turn the face toward where it is looking
    void setFaceRotation(const Vec3& rotation) {
        face.setRotation(rotation);
    }
};

// Shared renderer instance, defined in main.cpp
extern VectorRenderer vectorRenderer;
