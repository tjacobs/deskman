#include "renderer.h"

void drawLargeDot(SDL_Renderer* renderer, int x, int y) {
    // Draw a 3x3 pattern for each point
    SDL_RenderDrawPoint(renderer, x-1, y-1);
    SDL_RenderDrawPoint(renderer, x, y-1);
    SDL_RenderDrawPoint(renderer, x+1, y-1);
    SDL_RenderDrawPoint(renderer, x-1, y);
    SDL_RenderDrawPoint(renderer, x, y);
    SDL_RenderDrawPoint(renderer, x+1, y);
    SDL_RenderDrawPoint(renderer, x-1, y+1);
    SDL_RenderDrawPoint(renderer, x, y+1);
    SDL_RenderDrawPoint(renderer, x+1, y+1);
}

namespace Projection {
    SDL_Point project(const Vec3& point, const Vec3& faceRotation) {
        // Apply face rotation first
        Vec3 rotated = point
            .rotateX(faceRotation.x)
            .rotateY(faceRotation.y)
            .rotateZ(faceRotation.z);
            
        // Simple perspective projection
        float perspective = 1.0f / (1.0f + rotated.z * 0.001f);
        return {
            static_cast<int>(rotated.x * perspective + screen_width/2),
            static_cast<int>(rotated.y * perspective + screen_height/2)
        };
    }
} 